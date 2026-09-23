"""
Markdown → HTML → Word COM → PDF 核心转换 — TDD §7.4 / ADR-5

流程：
1. Markdown → HTML（markdown 库）
2. HTML → Word 临时文档（pywin32 Word COM）
3. Word → PDF（Word COM SaveAs）
4. 清理临时 Word 文档

依赖：Windows + 本机 Microsoft Office（Word COM）。
"""

import os
import sys
import tempfile
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import markdown

from utils.logger_utils import get_logger

logger = get_logger(__name__)


def md_to_html(md_content: str) -> str:
    """
    将 Markdown 文本转换为 HTML。

    Args:
        md_content: Markdown 原文。

    Returns:
        HTML 字符串（含内联 CSS 样式，适配中文和表格）。
    """
    # 扩展：tables + fenced_code_blocks
    extensions = ["tables", "fenced_code", "toc"]
    html_body = markdown.markdown(md_content, extensions=extensions)

    # 包裹完整 HTML 文档，加入中文字体和表格样式
    html_full = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
body {{
    font-family: "Microsoft YaHei", "SimSun", Arial, sans-serif;
    font-size: 12pt;
    line-height: 1.8;
    margin: 2cm;
}}
h1, h2, h3, h4 {{
    color: #333;
    margin-top: 1.2em;
    margin-bottom: 0.6em;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}}
th, td {{
    border: 1px solid #ccc;
    padding: 6px 10px;
    text-align: left;
}}
th {{
    background-color: #f5f5f5;
    font-weight: bold;
}}
p {{
    margin: 0.5em 0;
}}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
    return html_full


def html_to_pdf_via_word(html_path: Path, pdf_path: Path) -> Path:
    """
    通过 Word COM 将 HTML 文件转换为 PDF。

    Args:
        html_path: HTML 文件路径。
        pdf_path: 输出 PDF 文件路径。

    Returns:
        生成的 PDF 文件路径。

    Raises:
        RuntimeError: 转换失败或非 Windows 环境。
    """
    if sys.platform != "win32":
        raise RuntimeError("PDF 转换依赖 Windows Word COM，当前平台不支持")

    import pythoncom
    import win32com.client

    word = None
    doc = None
    try:
        # 初始化 COM
        pythoncom.CoInitialize()

        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False

        # 打开 HTML 文件
        abs_html = str(html_path.resolve())
        doc = word.Documents.Open(abs_html)

        # 保存为 PDF (FileFormat=17 是 wdFormatPDF)
        abs_pdf = str(pdf_path.resolve())
        doc.SaveAs(abs_pdf, FileFormat=17)

        logger.info(f"PDF 已生成：{pdf_path}")
        return pdf_path

    except Exception as e:
        logger.error(f"Word COM 转 PDF 失败：{e}")
        raise RuntimeError(f"PDF 转换失败：{e}")

    finally:
        # 确保释放资源
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def convert_md_to_pdf_core(md_path: Path, pdf_path: Path) -> Path:
    """
    完整转换流程：Markdown → HTML → Word COM → PDF。

    Args:
        md_path: 源 Markdown 文件路径。
        pdf_path: 输出 PDF 文件路径。

    Returns:
        生成的 PDF 文件路径。
    """
    # 1. 读取 Markdown
    md_content = md_path.read_text(encoding="utf-8")

    # 2. Markdown → HTML
    html_content = md_to_html(md_content)

    # 3. 写 HTML 到临时文件
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, encoding="utf-8", mode="w"
    ) as tmp_html:
        tmp_html.write(html_content)
        tmp_html_path = Path(tmp_html.name)

    try:
        # 4. HTML → PDF (Word COM)
        result = html_to_pdf_via_word(tmp_html_path, pdf_path)
        return result
    finally:
        # 清理临时 HTML 文件
        try:
            tmp_html_path.unlink()
        except Exception:
            pass


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    # 测试 md_to_html
    sample_md = """# 测试报告

## 概述

这是一份测试报告。

| 项目 | 数值 |
|------|------|
| A    | 100  |
| B    | 200  |
"""
    html = md_to_html(sample_md)
    print(f"[OK] md_to_html: {len(html)} chars")
    print(f"  HTML preview: {html[:200]}...")
