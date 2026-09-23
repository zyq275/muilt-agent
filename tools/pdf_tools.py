"""
MD→PDF 工具 — TDD §7.4

签名（照抄 TDD §7.4）：
    convert_md_to_pdf(md_filename: str, pdf_filename: Optional[str] = None) -> str

行为：校验源文件存在 → 调用转换核心生成 PDF。
归属：主智能体。
"""

import os
import sys
from pathlib import Path
from typing import Optional

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api.context import get_session_dir
from utils.logger_utils import get_logger
from utils.path_utils import OUTPUT_DIR, safe_resolve
from utils.converter_utils import convert_md_to_pdf_core

logger = get_logger(__name__)


def convert_md_to_pdf(md_filename: str, pdf_filename: Optional[str] = None) -> str:
    """
    将会话工作目录中的 Markdown 文件转换为 PDF。

    Args:
        md_filename: 源 Markdown 文件名（在工作目录内查找）。
        pdf_filename: 输出 PDF 文件名，默认与源文件同名但后缀为 .pdf。

    Returns:
        成功：f"已成功创建 {pdf_filename}"
        失败：f"转换 PDF 失败：{错误}"
    """
    try:
        # 获取会话工作目录
        session_dir = get_session_dir()
        if session_dir is None:
            session_dir = OUTPUT_DIR / "default"

        # 定位源 Markdown 文件
        # 清洗 → 拼接 → 嵌套检查 → 越界校验（TDD §14 路径穿越）：
        # 绝不能用 session_dir / sanitize_path(x)，理由见 markdown_tools。
        md_path = safe_resolve(md_filename, session_dir)

        if not md_path.exists():
            return f"错误：文件不存在 {md_filename}"

        # 确定 PDF 文件名（缺省取源文件同名）
        if pdf_filename is None:
            pdf_filename = md_path.stem + ".pdf"
        pdf_path = safe_resolve(pdf_filename, session_dir)

        # 调用核心转换
        convert_md_to_pdf_core(md_path, pdf_path)

        logger.info(f"PDF 已生成：{pdf_path}")
        return f"已成功创建 {pdf_filename}"

    except Exception as e:
        logger.error(f"convert_md_to_pdf 失败：{e}")
        return f"转换 PDF 失败：{e}"


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    from api.context import set_session
    from tools.markdown_tools import generate_markdown

    set_session("test_pdf")

    # 先生成一个 Markdown
    md_content = """# 测试 PDF 报告

## 概述

这是一份用于验证 PDF 转换功能的测试报告。

## 数据分析

| 药品   | 销量  | 金额     |
|--------|-------|----------|
| 阿莫西林 | 1000  | 25000.00 |
| 布洛芬   | 2000  | 30000.00 |

## 结论

PDF 转换功能验证通过。
"""
    md_result = generate_markdown(md_content, "test_pdf_report.md")
    print(f"1. {md_result}")

    # 再转为 PDF
    pdf_result = convert_md_to_pdf("test_pdf_report.md")
    print(f"2. {pdf_result}")

    # 验证 PDF 文件存在
    from api.context import get_session_dir
    session_dir = get_session_dir()
    if session_dir:
        pdf_files = list(session_dir.glob("*.pdf"))
        print(f"3. 目录下 PDF 文件: {[f.name for f in pdf_files]}")
