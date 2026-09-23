"""
上传文件解析工具 — TDD §7.4

签名（照抄 TDD §7.4）：
    read_file_content(filename: str, instruction: str = "提取全部内容") -> str

行为：按后缀分发解析
- .md / .txt → 纯文本读取
- .docx → python-docx 段落提取
- .pdf → pypdf 逐页提取
- .xlsx / .xls → openpyxl/pandas 返回行列数 + 前5行 + 统计描述

归属：主智能体。
"""

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api.context import get_session_dir, get_thread_id
from utils.logger_utils import get_logger
from utils.path_utils import UPLOAD_DIR, sanitize_path, safe_resolve

logger = get_logger(__name__)


def _find_file(filename: str) -> Path | None:
    """
    在**本会话**的工作目录与上传目录中查找文件（TDD §14）。

    只查这两个根，且每个候选都经 safe_resolve 越界校验。刻意不做两件事：

    1. 不做"任意绝对路径"兜底。那等于给智能体开了全盘读取权限——
       `read_file_content("C:/某处/资料.txt")` 能读走机器上任何文本类文件，
       与"子智能体文件访问受限在工作目录"直接冲突。
    2. 不遍历其他会话的上传目录。原实现扫 UPLOAD_DIR 下所有 session_*，
       会让 A 会话读到 B 会话上传的文件，破坏 TDD §14 的会话隔离。

    Args:
        filename: 文件名或相对路径（相对于本会话工作目录 / 上传目录）。

    Returns:
        找到的文件绝对路径，或 None。
    """
    roots: list[Path] = []

    session_dir = get_session_dir()
    if session_dir is not None:
        roots.append(session_dir)

    thread_id = get_thread_id()
    if thread_id:
        own_upload = UPLOAD_DIR / f"session_{sanitize_path(thread_id)}"
        if own_upload.is_dir():
            roots.append(own_upload)

    for root in roots:
        try:
            candidate = safe_resolve(filename, root)
        except ValueError as e:
            logger.warning(f"read_file_content 越界访问被拒绝：{filename!r}（{e}）")
            continue
        if candidate.is_file():
            return candidate

    return None


def _read_text(file_path: Path) -> str:
    """读取纯文本文件。"""
    return file_path.read_text(encoding="utf-8")


def _read_docx(file_path: Path) -> str:
    """用 python-docx 读取 Word 文档。"""
    from docx import Document

    doc = Document(str(file_path))
    paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]

    # 也读取表格内容
    tables_text = []
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            tables_text.append(row_text)

    result = "\n".join(paragraphs)
    if tables_text:
        result += "\n\n--- 表格 ---\n" + "\n".join(tables_text)
    return result


def _read_pdf(file_path: Path) -> str:
    """用 pypdf 逐页提取 PDF 文本。"""
    from pypdf import PdfReader

    reader = PdfReader(str(file_path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- 第 {i + 1} 页 ---\n{text}")

    return "\n\n".join(pages)


def _read_xlsx(file_path: Path) -> str:
    """用 pandas + openpyxl 读取 Excel，返回行列数 + 前5行 + 统计描述。"""
    import pandas as pd

    df = pd.read_excel(str(file_path), engine="openpyxl")

    lines = []
    lines.append(f"行数: {len(df)}, 列数: {len(df.columns)}")
    lines.append(f"列名: {', '.join(str(c) for c in df.columns)}")

    # 前 5 行
    lines.append("\n--- 前 5 行 ---")
    lines.append(df.head(5).to_string())

    # 统计描述（仅数值列）
    numeric_cols = df.select_dtypes(include="number")
    if not numeric_cols.empty:
        lines.append("\n--- 统计描述 ---")
        lines.append(numeric_cols.describe().to_string())

    return "\n".join(lines)


def read_file_content(filename: str, instruction: str = "提取全部内容") -> str:
    """
    读取上传/工作目录中的文件内容，按后缀自动分发解析。

    Args:
        filename: 文件名（在工作目录或上传目录中查找）。
        instruction: 读取指令（保留参数，未来可基于指令选择性提取），默认"提取全部内容"。

    Returns:
        解析后的文本内容；文件不存在返回"错误：文件不存在 ..."；不支持的格式返回提示。
    """
    try:
        file_path = _find_file(filename)
        if file_path is None:
            return f"错误：文件不存在 {filename}"

        suffix = file_path.suffix.lower()

        if suffix in (".md", ".txt"):
            content = _read_text(file_path)
        elif suffix == ".docx":
            content = _read_docx(file_path)
        elif suffix == ".pdf":
            content = _read_pdf(file_path)
        elif suffix in (".xlsx", ".xls"):
            content = _read_xlsx(file_path)
        else:
            return f"不支持的文件格式：{suffix}（支持 .md/.txt/.docx/.pdf/.xlsx/.xls）"

        logger.info(f"read_file_content('{filename}') 成功，{len(content)} 字符")
        return content

    except Exception as e:
        logger.error(f"read_file_content 失败：{e}")
        return f"读取文件失败：{e}"


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    from api.context import set_session

    set_session("test_read")

    # 测试 1：读取 .md 文件
    from tools.markdown_tools import generate_markdown

    generate_markdown("# 测试文件\n\n这是一个测试。", "read_test.md")

    result = read_file_content("read_test.md")
    print("=== read .md ===")
    print(result[:200])
    print()

    # 测试 2：读取不存在的文件
    result = read_file_content("nonexistent.txt")
    print("=== read nonexistent ===")
    print(result)
    print()

    # 测试 3：读取 .xlsx（如果有）
    import pandas as pd

    session_dir = get_session_dir()
    if session_dir:
        xlsx_path = session_dir / "test_data.xlsx"
        df = pd.DataFrame(
            {"药品": ["阿莫西林", "布洛芬", "二甲双胍"], "销量": [1000, 2000, 500], "单价": [25.0, 15.0, 35.0]}
        )
        df.to_excel(str(xlsx_path), index=False, engine="openpyxl")

        result = read_file_content("test_data.xlsx")
        print("=== read .xlsx ===")
        print(result)
