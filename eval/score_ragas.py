"""
RAGAS 生成层评分 — TDD §7.3 / §9.4

读 eval/eval_data.json（由 scripts/eval_rag.py --dump 导出），
用 RAGAS 计算四个指标：

    Faithfulness       答案是否忠实于检索到的上下文（有无编造）
    Answer Relevancy   答案是否切题（对得上问题）
    Context Precision  检索到的上下文里，相关内容的排序质量
    Context Recall     标准答案所需的信息，是否被检索到了

**必须在 eval/.venv 中运行**，不能在主环境：
ragas 需要 langchain_community.chat_models.vertexai（0.4.x 已删除），
而把它降级回 0.3.x 会把 langchain-core 拖回 0.3，与 langgraph/deepagents
要求的 ≥1.4 冲突。两套 langchain 栈无法共存，所以评分隔离在一个专用 venv 里。

用法（项目根目录下）：
    python eval/score_ragas.py            # 全量 10 条
    python eval/score_ragas.py --limit 2  # 先跑 2 条试水

两个解释器都能跑：脚本开头有解释器守卫，发现当前 Python 里没有 ragas
会自动用 eval/.venv 的解释器重启自己，所以不必记住 venv 的路径。
"""

# ══════════════════════════════════════════════════════════════════════
# 兼容补丁：必须在 import ragas 之前执行
#
# ragas/llms/base.py 第 12 行有一句无保护的
#     from langchain_community.chat_models.vertexai import ChatVertexAI
# 而 langchain-community 0.4.x 把这个模块删掉了（同版本的 llms.vertexai
# 却还在，属删除不彻底）。于是仅靠 import ragas 就会 ModuleNotFoundError。
#
# 这句导入的用途只有一个：把它放进 MULTIPLE_COMPLETION_SUPPORTED 列表，
# 供 isinstance 判断"该 LLM 是否支持 n 参数多候选生成"。我们用的是
# ChatOpenAI，永远不会是 ChatVertexAI，因此用一个空壳类占位在语义上
# 完全等价——isinstance 返回 False，正是应有的答案。
#
# 选择在脚本里注入 sys.modules，而不是往 site-packages 里塞文件：
# 补丁跟着仓库走、可评审、可随 venv 重建自动生效，也不污染主环境。
# 一旦 ragas 修掉这行导入，下面的判断会自动跳过，无需手工清理。
# ══════════════════════════════════════════════════════════════════════
import importlib.util
import os
import sys
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
# 解释器守卫：必须在 import ragas 之前执行
#
# ragas 只装在 eval/.venv 里，主环境没有。用错解释器时（IDE 的 Run 按钮、
# 手敲 python eval/score_ragas.py）会直接 ModuleNotFoundError，而这个错误
# 长得像"代码有问题"，实际只是跑错了 Python。
#
# 这里检测到当前解释器没有 ragas，就用 venv 的解释器重启自己，让调用者
# 不必记住路径。属"魔法"，但收益明确：这条命令已经被踩中两次了。
#
# 用环境变量做重入标记，避免 venv 里也缺 ragas 时无限重启。
# 若不想被自动切换，设 TANYUAN_NO_REEXEC=1 即可拿到原始报错。
# ══════════════════════════════════════════════════════════════════════
_VENV_PY = (
    Path(__file__).resolve().parent
    / ".venv"
    / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)

if (
    importlib.util.find_spec("ragas") is None
    and not os.environ.get("_TANYUAN_VENV_REEXEC")
    and not os.environ.get("TANYUAN_NO_REEXEC")
):
    if _VENV_PY.exists():
        # 必须 flush：os.execv 直接替换进程镜像，不会刷新 Python 的 stdout
        # 缓冲区。输出重定向到文件/管道时是块缓冲，不加 flush 这句话会被丢掉。
        print(f"[score_ragas] 当前解释器 {sys.executable} 中没有 ragas，"
              f"自动切换到 {_VENV_PY}", flush=True)
        os.environ["_TANYUAN_VENV_REEXEC"] = "1"
        try:
            os.execv(str(_VENV_PY),
                     [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
        except OSError as e:
            sys.exit(f"[score_ragas] 切换到 venv 失败：{e}\n"
                     f"请手动运行：\"{_VENV_PY}\" eval/score_ragas.py")
    else:
        sys.exit(
            f"[score_ragas] 当前解释器中没有 ragas，且隔离环境不存在：{_VENV_PY}\n"
            "请先创建：\n"
            "    python -m venv eval/.venv\n"
            "    eval/.venv/Scripts/python -m pip install -r eval/requirements.txt"
        )

if importlib.util.find_spec("langchain_community.chat_models.vertexai") is None:
    import types

    _shim = types.ModuleType("langchain_community.chat_models.vertexai")

    class ChatVertexAI:  # noqa: D401 - 占位，见上方说明
        """langchain-community 0.4.x 已移除该类的占位符。"""

    _shim.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = _shim

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import warnings  # noqa: E402

# ragas 0.4 把四个指标的单例都标记为待迁移（v1.0 移除），但替代品要手工
# 实例化再逐个注入 llm，配置更啰嗦。这里仍用旧单例，只屏蔽这一族特定的
# 导入告警，别把真正有用的警告一起吞掉。
warnings.filterwarnings(
    "ignore",
    message=r"Importing \w+ from 'ragas\.metrics' is deprecated.*",
    category=DeprecationWarning,
)

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import openai  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from ragas import EvaluationDataset, SingleTurnSample, evaluate  # noqa: E402
from ragas.embeddings.base import BaseRagasEmbeddings  # noqa: E402
from ragas.llms import llm_factory  # noqa: E402
from ragas.metrics import (  # noqa: E402
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)
from ragas.run_config import RunConfig  # noqa: E402

load_dotenv()

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_IN = EVAL_DIR / "eval_data.json"
DEFAULT_OUT = EVAL_DIR / "ragas_result.json"

METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]


def _is_missing(v) -> bool:
    """
    判断某个指标值是否算"没算出来"。

    只判 None 是不够的：RAGAS 在单条指标失败时会返回 nan 而不是 None。
    而 nan 参与算术会静默传染——求均值得到 nan，显示成 "--" 也好、
    混进均值也好，都会让报告看起来正常实则全是废数。必须一并当缺失处理。
    """
    return v is None or (isinstance(v, float) and math.isnan(v))


class LocalBgeEmbeddings(BaseRagasEmbeddings):
    """
    RAGAS 的 embedding 适配器，直接包 sentence-transformers。

    为什么不直接用 ragas 自带的 HuggingFaceEmbeddings：它内部包的是
    langchain_huggingface，而本 venv 没装那个包；装了也有版本冲突风险。
    RAGAS 只要求能 embed 出向量，直接接 sentence-transformers 更省事。

    关键：normalize_embeddings=True 必须与 build_knowledge_base.py 中
    建库时的取法一致，否则查询向量与库向量不在同一尺度上，算出来的
    相似度没有可比性。
    """

    def __init__(self, model_path: str):
        super().__init__()
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_path)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)


def build_llm():
    """
    构造 RAGAS 用的裁判模型（DeepSeek，走 OpenAI 兼容协议）。

    RAGAS 用 instructor 把 OpenAI 客户端包一层，让模型输出结构化 JSON。

    裁判模型取自 RAGAS_JUDGE_MODEL，与生成答案用的 DEEPSEEK_MODEL 分开配置：
    同一个模型既生成答案又给自己打分，会系统性偏乐观（它倾向于认为自己
    写的东西忠实、切题）。换成另一个模型能明显削弱这层同源偏差。
    该键留空时回退到 DEEPSEEK_MODEL，行为与未引入该配置时一致。
    """
    judge = os.getenv("RAGAS_JUDGE_MODEL") or os.getenv("DEEPSEEK_MODEL",
                                                       "deepseek-chat")
    generator = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    print(f"裁判模型：{judge}")
    if judge == generator:
        print(f"⚠️  裁判模型与生成模型相同（{judge}），"
              "评分会偏乐观；可在 .env 中设置 RAGAS_JUDGE_MODEL 区分两者")

    client = openai.OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL"),
    )
    # max_tokens 必须显式调大：RAGAS 默认只给 1024，而 DeepSeek 的模型带思考模式，
    # 推理过程本身就要吃掉大量 token，1024 会在裁判输出结构化结果之前就截断，
    # 抛 IncompleteOutputException，该条指标直接变成 nan。
    # （ragas 自己的注释也提示这一点，见 ragas/llms/base.py:820）
    return llm_factory(judge, client=client, max_tokens=8192)


def build_embeddings() -> LocalBgeEmbeddings:
    """加载与建库时同一个 BGE 模型（优先用本地已下载的路径）。"""
    model_path = os.getenv("EMBEDDING_MODEL_PATH") or os.getenv(
        "EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5"
    )
    print(f"加载 embedding 模型：{model_path}")
    return LocalBgeEmbeddings(model_path)


def load_rows(path: Path = DEFAULT_IN) -> dict:
    """
    读取 eval_rag.py 导出的评估数据。

    Args:
        path: eval_data.json 路径。

    Returns:
        完整 payload（含 model / hit_rate / rows）。

    Raises:
        FileNotFoundError: 文件不存在（还没跑过 --dump）。
        ValueError: 数据为空。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"评估数据不存在：{path}\n"
            "请先在主环境运行：python scripts/eval_rag.py --dump"
        )
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    rows = payload.get("rows", [])
    if not rows:
        raise ValueError(f"评估数据为空：{path}")
    return payload


def build_dataset(rows: list[dict]) -> EvaluationDataset:
    """把导出的行转成 RAGAS 的样本对象。"""
    samples = [
        SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["ground_truth"],
        )
        for r in rows
    ]
    return EvaluationDataset(samples=samples)


def print_report(rows: list[dict], scores: list[dict]) -> dict:
    """
    打印每条得分与总体均值。

    Args:
        rows: 原始评估行（取 id / question）。
        scores: RAGAS 返回的逐条得分字典。

    Returns:
        {指标名: 均值}，跳过 None（该项计算失败的条目）。
    """
    print()
    print("=" * 92)
    print("RAGAS 生成层评估（Faithfulness / Answer Relevancy / "
          "Context Precision / Context Recall）")
    print("=" * 92)

    header = f"{'ID':<5}{'Faith':>9}{'AnsRel':>9}{'CtxPrec':>9}{'CtxRec':>9}   问题"
    print(header)
    print("-" * 92)

    for row, score in zip(rows, scores):
        cells = ""
        for name in METRIC_NAMES:
            v = score.get(name)
            cells += f"{'--':>9}" if _is_missing(v) else f"{v:>9.3f}"
        q = row["question"]
        if len(q) > 40:
            q = q[:39] + "…"
        print(f"{row['id']:<5}{cells}   {q}")

    print("-" * 92)
    means = {}
    mean_cells = ""
    for name in METRIC_NAMES:
        vals = [s[name] for s in scores if not _is_missing(s.get(name))]
        # 无有效样本时写 None 而不是 nan，JSON 才能正常序列化
        means[name] = sum(vals) / len(vals) if vals else None
        mean_cells += (
            f"{'--':>9}" if means[name] is None else f"{means[name]:>9.3f}"
        )
    print(f"{'均值':<5}{mean_cells}")

    failed = [r["id"] for r, s in zip(rows, scores)
              if any(_is_missing(s.get(n)) for n in METRIC_NAMES)]
    if failed:
        print()
        print(f"⚠️  以下条目存在计算失败的指标（显示为 --）：{', '.join(failed)}")
        print("   常见原因：模型未按结构化格式返回、上下文为空、网络超时。")

    return means


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS 生成层评分（TDD §9.4）")
    parser.add_argument("--in", dest="in_path", default=str(DEFAULT_IN),
                        help=f"输入文件，默认 {DEFAULT_IN.name}")
    parser.add_argument("--out", dest="out_path", default=str(DEFAULT_OUT),
                        help=f"评分结果输出，默认 {DEFAULT_OUT.name}")
    parser.add_argument("--limit", type=int, default=0,
                        help="只评前 N 条（试水用），0 表示全量")
    args = parser.parse_args()

    payload = load_rows(Path(args.in_path))
    rows = payload["rows"]
    if args.limit:
        rows = rows[: args.limit]

    print(f"评估数据：{args.in_path}")
    print(f"条数：{len(rows)}")
    print(f"生成模型：{payload.get('model', '?')}")
    print(f"检索命中率（来自 eval_rag.py）：{payload.get('hit_rate', 0):.2%}")

    llm = build_llm()
    embeddings = build_embeddings()

    # Answer Relevancy 默认造 3 个问题变体再取余弦相似度均值（strictness=3），
    # 但那要求模型支持 n=3 多候选生成。DeepSeek 不支持，只会回 1 个，
    # RAGAS 便打印 "LLM returned 1 generations instead of requested 3" 后
    # 按 1 个继续算。结果与 strictness=1 完全等价，与其让它静默降级、
    # 不如显式声明，让配置和实际行为一致。
    answer_relevancy.strictness = 1

    print("\nRAGAS 评分中（每条 4 个指标，需调用大模型，请稍候）...")
    result = evaluate(
        dataset=build_dataset(rows),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
        # DeepSeek 偶尔会卡住；10 条 × 4 指标并发 4 路，留足超时余量
        run_config=RunConfig(max_workers=4, timeout=300),
        # 单条失败不要炸掉整轮，先把能算出来的分拿到手
        raise_exceptions=False,
        show_progress=True,
    )

    scores = result.scores
    means = print_report(rows, scores)

    out_path = Path(args.out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": payload.get("model", ""),
                "retrieval_hit_rate": payload.get("hit_rate", 0.0),
                "limit": args.limit,
                "means": means,
                # nan 归一成 null：json.dump 默认会把 nan 写成裸的 NaN，
                # 那不是合法 JSON，其它语言的标准解析器读不了。
                "per_question": [
                    {
                        "id": r["id"],
                        "question": r["question"],
                        **{k: (None if _is_missing(v) else v)
                           for k, v in s.items()},
                    }
                    for r, s in zip(rows, scores)
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n评分结果已写入：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
