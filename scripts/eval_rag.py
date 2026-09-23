"""
RAG 质量评估 — TDD §9.4

分两层，跑在两个环境里：

1. 检索层（本脚本，主环境）
   读 eval/eval_questions.json，对每条 query 调 search_knowledge_base，
   检查返回片段里是否出现 expected_source，算检索命中率 = 命中条数 / 总条数。
   命中率低于阈值 0.7 时提示调整分块参数或 embedding 模型。
   不依赖 LLM 当裁判，简单可解释。

2. 生成层（进阶，LLM-as-judge，RAGAS）
   Faithfulness / Answer Relevance / Context Precision / Context Recall。

   为什么要分环境：ragas 依赖 langchain_community.chat_models.vertexai，
   该模块在 langchain-community 0.4.x 中已移除；降级 langchain-community
   又会把 langchain-core 拖回 0.3.x，而 langgraph / deepagents 要求
   langchain-core ≥ 1.4。两套 langchain 栈同环境无法共存。

   所以本脚本用 `--dump` 导出评估数据（检索上下文 + 生成答案 + 参考答案），
   再由 eval/score_ragas.py 在隔离 venv 中读该文件跑 RAGAS。

用法：
    python scripts/eval_rag.py                       # 只跑检索层
    python scripts/eval_rag.py --dump                # 检索层 + 导出评估数据
    python scripts/eval_rag.py --dump eval/data.json # 指定导出路径
    # 然后（在 eval/.venv 中）：
    #   python eval/score_ragas.py
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv

from tools.knowledge_base_tool import search_knowledge_base
from utils.logger_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
QUESTIONS_PATH = EVAL_DIR / "eval_questions.json"
DEFAULT_DUMP_PATH = EVAL_DIR / "eval_data.json"

# 命中率阈值（TDD §9.4）
HIT_RATE_THRESHOLD = 0.7

# 检索深度。用工具默认值 4，让评估反映真实生产配置——
# 调大 k 能让命中率好看，但线上就是 k=4，评估应当对齐线上。
RETRIEVAL_K = 4

# 从返回文本里抽来源文件名。工具的输出格式固定为 "[来源: 文件名]"
_SOURCE_RE = re.compile(r"\[来源:\s*([^\]]+)\]")

# 生成层用的参考答案（ground truth）。
# Context Recall 需要它才能判定"该被检索到的内容有没有被检索到"。
GROUND_TRUTHS = {
    "q01": "阿莫西林用于敏感菌引起的上呼吸道感染（中耳炎、鼻窦炎、咽炎、扁桃体炎）、"
           "下呼吸道感染（支气管炎、肺炎）、泌尿生殖道感染（膀胱炎、尿道炎、肾盂肾炎）、"
           "皮肤软组织感染，以及与克拉霉素、兰索拉唑三联用药根除幽门螺杆菌。",
    "q02": "布洛芬禁用于消化道溃疡患者、严重肝肾功能不全者、孕晚期妇女。"
           "成人一次 0.2g、一日 3~4 次，最大剂量一日不超过 2.4g。",
    "q03": "冷链药品储存条件要求 2~8℃，冷库须配备双路供电或备用发电机组，"
           "断电后保温时长不低于 4 小时。",
    "q04": "常温库 10~30℃；阴凉库不高于 20℃；冷库 2~8℃。"
           "相对湿度均为 35%~75%（冷库 45%~75%）。",
    "q05": "处方开具当日有效。特殊情况需延长的，由医师在处方上注明有效期限，"
           "但最长不得超过 3 天。",
    "q06": "麻醉药品和第一类精神药品处方保存 3 年。",
    "q07": "新的、严重的不良反应 15 日内上报；死亡病例立即报告，最迟不超过 24 小时。",
    "q08": "六分法为：肯定、很可能、可能、可能无关、待评价、无法评价。"
           "判断要点：用药与反应的时间顺序是否合理、是否为该药已知不良反应、"
           "停药后是否减轻或消失、再次用药是否重现。",
    "q09": "特殊使用级须经具有高级专业技术职务任职资格的医师会诊同意后开具，"
           "且不得在门诊使用。",
    "q10": "外科预防用药应在切皮前 0.5~2 小时内给药，总预防用药时间一般不超过 24 小时。",
}


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    """
    读取评估集。

    Args:
        path: eval_questions.json 路径。

    Returns:
        问题列表，每项含 id / query / expected_source。

    Raises:
        FileNotFoundError: 评估集不存在。
        ValueError: 评估集为空或缺少必需字段。
    """
    if not path.exists():
        raise FileNotFoundError(f"评估集不存在：{path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = data.get("questions", [])
    if not questions:
        raise ValueError(f"评估集为空：{path}")

    for q in questions:
        missing = {"query", "expected_source"} - set(q)
        if missing:
            raise ValueError(f"评估集条目缺少字段 {missing}：{q}")

    return questions


def evaluate_retrieval(questions: list[dict], k: int = RETRIEVAL_K) -> dict:
    """
    逐条检索并判断是否命中期望来源。

    Args:
        questions: 问题列表。
        k: 传给 search_knowledge_base 的 top-k。

    Returns:
        {"details": [...], "hit_count": int, "total": int, "hit_rate": float}
    """
    details = []

    for item in questions:
        query = item["query"]
        expected = item["expected_source"]

        result_text = search_knowledge_base(query, k=k)
        retrieved = _SOURCE_RE.findall(result_text)

        # 去重但保持顺序：同一个来源可能占据多个片段
        seen = []
        for s in retrieved:
            s = s.strip()
            if s not in seen:
                seen.append(s)

        hit = expected in seen
        details.append({
            "id": item.get("id", ""),
            "query": query,
            "expected": expected,
            "retrieved": seen,
            "hit": hit,
            # 排在第几位命中（1 起）。未命中为 None。
            "rank": (seen.index(expected) + 1) if hit else None,
            "raw": result_text,
        })

    hit_count = sum(1 for d in details if d["hit"])
    total = len(details)
    return {
        "details": details,
        "hit_count": hit_count,
        "total": total,
        "hit_rate": hit_count / total if total else 0.0,
    }


def print_retrieval_report(report: dict, k: int = RETRIEVAL_K) -> None:
    """打印每条命中情况与总体命中率。"""
    print()
    print("=" * 78)
    print(f"检索层评估（top-k = {k}，阈值 {HIT_RATE_THRESHOLD}）")
    print("=" * 78)

    for d in report["details"]:
        mark = "✅ 命中" if d["hit"] else "❌ 未命中"
        got = ", ".join(d["retrieved"]) if d["retrieved"] else "（无返回）"
        print(f"\n{mark}  [{d['id']}] {d['query']}")
        print(f"        期望来源：{d['expected']}")
        print(f"        实际返回：{got}")
        if d["hit"]:
            print(f"        命中位次：第 {d['rank']} 位")

    print()
    print("-" * 78)
    print(f"命中 {report['hit_count']} / {report['total']}")
    print(f"总体命中率：{report['hit_rate']:.2%}")

    if report["hit_rate"] < HIT_RATE_THRESHOLD:
        print()
        print(f"⚠️  命中率 {report['hit_rate']:.2%} 低于阈值 {HIT_RATE_THRESHOLD:.0%}，"
              "建议调整分块参数或 embedding 模型")
    else:
        print(f"✅ 命中率不低于阈值 {HIT_RATE_THRESHOLD:.0%}，检索质量达标")


def _split_fragments(result_text: str) -> list[str]:
    """
    把 search_knowledge_base 的返回文本拆回片段列表（去掉 [来源: x] 行）。

    必须按 "[来源: " 标记切分，不能按空行切：片段正文本身是 Markdown，
    内部就含空行（段落、列表之间），按空行切会把一个片段打成十几个碎块，
    喂给 RAGAS 会让上下文条数虚高几十倍，Context Precision / Recall 失去意义。
    """
    if not result_text or result_text.startswith("知识库"):
        return []

    # 在每个 "[来源: " 之前切开（零宽断言，不消耗标记本身）
    parts = [p for p in re.split(r"(?=\[来源:)", result_text) if p.strip()]

    cleaned = []
    for p in parts:
        lines = [ln for ln in p.splitlines() if not ln.strip().startswith("[来源:")]
        text = "\n".join(lines).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _generate_answer(query: str, contexts: list[str]) -> str:
    """
    用大模型基于检索上下文生成答案（生成层的输入之一）。

    这里刻意用"仅依据资料回答"的提示，让 Faithfulness 衡量的是
    "模型有没有忠实于检索到的上下文"，而不是它的先验知识。
    """
    from agent.llm import get_llm

    ctx = "\n\n".join(contexts)
    prompt = (
        "请仅依据下面提供的资料回答问题。资料中没有的内容不要编造，"
        "确实找不到依据时回答“资料中未提及”。\n\n"
        f"【资料】\n{ctx}\n\n【问题】\n{query}\n\n【回答】"
    )
    return get_llm().invoke(prompt).content


def dump_eval_data(report: dict, path: Path = DEFAULT_DUMP_PATH) -> Path:
    """
    导出 RAGAS 评估数据（供隔离 venv 中的 score_ragas.py 读取）。

    Args:
        report: evaluate_retrieval 的结果。
        path: 导出路径。

    Returns:
        实际写入的路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for d in report["details"]:
        contexts = _split_fragments(d["raw"])
        print(f"  生成答案中：{d['id']} ...", end="", flush=True)
        answer = _generate_answer(d["query"], contexts)
        print(" 完成")
        rows.append({
            "id": d["id"],
            "question": d["query"],
            "contexts": contexts,
            "answer": answer,
            "ground_truth": GROUND_TRUTHS.get(d["id"], ""),
        })

    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", ""),
        "retrieval_k": RETRIEVAL_K,
        "hit_rate": report["hit_rate"],
        "rows": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"评估数据已导出：{path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 评估（TDD §9.4）")
    parser.add_argument("-k", type=int, default=RETRIEVAL_K,
                        help=f"检索 top-k，默认 {RETRIEVAL_K}")
    parser.add_argument("--dump", nargs="?", const=str(DEFAULT_DUMP_PATH),
                        default=None, metavar="PATH",
                        help="额外导出 RAGAS 评估数据（默认 eval/eval_data.json）")
    args = parser.parse_args()

    questions = load_questions()
    print(f"评估集：{QUESTIONS_PATH}")
    print(f"问题数：{len(questions)}")

    report = evaluate_retrieval(questions, k=args.k)
    print_retrieval_report(report, k=args.k)

    if args.dump:
        print()
        print("=" * 78)
        print("导出 RAGAS 评估数据")
        print("=" * 78)
        out = dump_eval_data(report, Path(args.dump))
        print(f"\n已写入：{out}")
        print("下一步（在隔离环境中）：")
        print("    eval/.venv/Scripts/python eval/score_ragas.py")

    # 命中率不达标时以非零码退出，便于 CI 捕获
    return 0 if report["hit_rate"] >= HIT_RATE_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
