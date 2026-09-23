"""
反思循环硬校验器 — TDD §5.2

实现 H1~H4 四项程序级、确定性校验（非工具，供编排层调用）：

| 编号 | 校验项     | 判定规则                                              | 不通过提示                |
| ---- | ---------- | ----------------------------------------------------- | ------------------------- |
| H1   | 篇幅       | 报告正文字数 < 1000                                   | "报告篇幅不足 1000 字"     |
| H2   | 占位符     | 含"待补充/TODO/等待子任务/占位/（略）"等关键词          | "报告存在未填充占位符"     |
| H3   | 格式       | 用户要求 PDF 但未产出 PDF 文件                        | "未生成要求的 PDF 文件"    |
| H4   | 数据源覆盖 | 任务要求的信息来源未被检索                             | "缺少 {数据源} 的检索结果" |

归属：编排层。
"""

import re
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from utils.logger_utils import get_logger

logger = get_logger(__name__)

# ── 常量 ──────────────────────────────────────────────

# H1：报告最少字数
MIN_WORD_COUNT = 1000

# H2：占位符关键词（大小写不敏感匹配英文部分）
PLACEHOLDER_KEYWORDS = [
    "待补充",
    "TODO",
    "待办",
    "等待子任务",
    "等待子任务完成",
    "占位",
    "（略）",
    "(略)",
    "待完善",
    "此处省略",
    "XXX",
]

# H4：数据源展示名
SOURCE_LABELS = {
    "web_search": "网络公开信息",
    "db_query": "企业结构化业务数据",
    "knowledge": "企业内部知识文档",
}


# ── H1：篇幅 ──────────────────────────────────────────

def count_report_words(text: str) -> int:
    """
    统计报告正文字数。

    去除 Markdown 标记符号与所有空白后，统计有效字符数
    （中文字符按 1 字计，英文按字符计）。

    Args:
        text: 报告全文。

    Returns:
        有效字数。
    """
    if not text:
        return 0

    # 去除 Markdown 语法符号
    cleaned = re.sub(r"[#*`>|_~\-\[\]\(\)!]", "", text)
    # 去除所有空白字符
    cleaned = re.sub(r"\s+", "", cleaned)
    return len(cleaned)


def check_h1(report_text: str) -> str | None:
    """
    H1 篇幅校验：报告正文字数 < 1000 则不通过。

    Args:
        report_text: 报告全文。

    Returns:
        未通过时返回提示文案，通过返回 None。
    """
    word_count = count_report_words(report_text)
    if word_count < MIN_WORD_COUNT:
        return f"报告篇幅不足 1000 字（当前约 {word_count} 字），请补充内容"
    return None


# ── H2：占位符 ────────────────────────────────────────

def check_h2(report_text: str) -> str | None:
    """
    H2 占位符校验：含未填充占位符关键词则不通过。

    Args:
        report_text: 报告全文。

    Returns:
        未通过时返回提示文案（含命中的关键词），通过返回 None。
    """
    if not report_text:
        return "报告存在未填充占位符（报告内容为空）"

    lowered = report_text.lower()
    hits = [kw for kw in PLACEHOLDER_KEYWORDS if kw.lower() in lowered]
    if hits:
        return f"报告存在未填充占位符：{', '.join(hits)}，请替换为实际内容"
    return None


# ── H3：格式（PDF 产出） ──────────────────────────────

def check_h3(require_pdf: bool, produced_files: list[str] | None = None) -> str | None:
    """
    H3 格式校验：用户要求 PDF 但工作目录中未产出 PDF 文件则不通过。

    Args:
        require_pdf: 用户是否要求 PDF。
        produced_files: 工作目录中实际产出的文件名列表。

    Returns:
        未通过时返回提示文案，通过返回 None。
    """
    if not require_pdf:
        return None

    files = produced_files or []
    has_pdf = any(name.lower().endswith(".pdf") for name in files)
    if not has_pdf:
        return "未生成要求的 PDF 文件，请先产出 Markdown 再调用 convert_md_to_pdf 转换"
    return None


# ── H4：数据源覆盖 ────────────────────────────────────

def check_h4(
    required_sources: set[str] | None = None,
    called_sources: set[str] | None = None,
) -> str | None:
    """
    H4 数据源覆盖校验：任务要求的信息来源未被检索则不通过。

    Args:
        required_sources: 规划阶段声明应当调用的数据源集合，
                          取值 {"web_search", "db_query", "knowledge"}。
        called_sources: 本轮实际调用了的数据源集合。

    Returns:
        未通过时返回提示文案（列出缺失数据源），通过返回 None。
    """
    required = required_sources or set()
    called = called_sources or set()

    missing = required - called
    if missing:
        labels = [SOURCE_LABELS.get(s, s) for s in sorted(missing)]
        return f"缺少 {'、'.join(labels)} 的检索结果，请调用对应子智能体补充检索"
    return None


# ── 汇总入口 ──────────────────────────────────────────

def hard_check(
    report_text: str,
    *,
    require_pdf: bool = False,
    produced_files: list[str] | None = None,
    required_sources: set[str] | None = None,
    called_sources: set[str] | None = None,
) -> list[str]:
    """
    执行 H1~H4 全部硬校验，返回未通过项列表。

    Args:
        report_text: 报告全文。
        require_pdf: 用户是否要求 PDF（H3）。
        produced_files: 工作目录中实际产出的文件名列表（H3）。
        required_sources: 规划阶段声明应调用的数据源集合（H4）。
        called_sources: 本轮实际调用的数据源集合（H4）。

    Returns:
        未通过项的提示文案列表；全部通过时返回空列表 []。
    """
    issues: list[str] = []

    h1 = check_h1(report_text)
    if h1:
        issues.append(h1)

    h2 = check_h2(report_text)
    if h2:
        issues.append(h2)

    h3 = check_h3(require_pdf, produced_files)
    if h3:
        issues.append(h3)

    h4 = check_h4(required_sources, called_sources)
    if h4:
        issues.append(h4)

    if issues:
        logger.info(f"硬校验未通过 {len(issues)} 项：{issues}")
    else:
        logger.info("硬校验全部通过")

    return issues


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    long_report = "阿莫西林是一种广谱抗生素，用于治疗敏感菌引起的感染。" * 60

    print("=== H1 篇幅 ===")
    print("短报告:", check_h1("太短了"))
    print("长报告:", check_h1(long_report))

    print("\n=== H2 占位符 ===")
    print("含占位符:", check_h2("本报告内容待补充，详见后续。"))
    print("无占位符:", check_h2(long_report))

    print("\n=== H3 格式 ===")
    print("要求PDF但无:", check_h3(True, ["report.md"]))
    print("要求PDF且有:", check_h3(True, ["report.md", "report.pdf"]))
    print("未要求PDF:", check_h3(False, []))

    print("\n=== H4 数据源 ===")
    print(
        "缺失知识库:",
        check_h4({"web_search", "db_query", "knowledge"}, {"web_search", "db_query"}),
    )
    print("全部覆盖:", check_h4({"web_search"}, {"web_search"}))

    print("\n=== hard_check 汇总 ===")
    print(hard_check("太短", require_pdf=True, required_sources={"knowledge"}))
    print(hard_check(long_report))
