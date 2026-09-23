"""
Markdown 生成工具 — TDD §7.4

签名（照抄 TDD §7.4）：
    generate_markdown(content: str, filename: str, path: str = "") -> str

行为：写入 Markdown 到会话工作目录，自动补 .md 后缀。
归属：主智能体。
"""

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api.context import get_session_dir
from utils.logger_utils import get_logger
from utils.path_utils import OUTPUT_DIR, safe_resolve

logger = get_logger(__name__)


def generate_markdown(content: str, filename: str, path: str = "") -> str:
    """
    生成 Markdown 文件并写入会话工作目录。

    Args:
        content: Markdown 文本内容。
        filename: 文件名（自动补 .md 后缀）。
        path: 子目录路径（相对于会话工作目录），默认为空（写入根目录）。

    Returns:
        成功：f"已成功创建 {filename}"
        失败：f"创建 Markdown 文件失败：{错误}"
    """
    try:
        # 获取会话工作目录，若无则回退到 output/default
        session_dir = get_session_dir()
        if session_dir is None:
            session_dir = OUTPUT_DIR / "default"
            session_dir.mkdir(parents=True, exist_ok=True)

        # 自动补 .md 后缀
        if not filename.lower().endswith(".md"):
            filename = filename + ".md"

        # 子目录 + 文件名合并为相对路径，一步完成
        # 清洗 → 拼接 → 嵌套检查 → 越界校验（TDD §14 路径穿越）。
        # 注意：这里绝不能用 session_dir / sanitize_path(x) ——
        # pathlib 在右侧为绝对路径（如 C:\Windows）时会整体替换左侧，
        # 且 sanitize_path 只负责清洗、不负责断言结果落在 root 内。
        rel_path = f"{path}/{filename}" if path else filename
        file_path = safe_resolve(rel_path, session_dir)

        # 写入文件（子目录按需创建）
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Markdown 已生成：{file_path}")
        return f"已成功创建 {filename}"

    except Exception as e:
        logger.error(f"generate_markdown 失败：{e}")
        return f"创建 Markdown 文件失败：{e}"


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    from api.context import set_session

    set_session("test_md")

    content = """# 测试报告

## 概述

这是一份测试报告，用于验证 Markdown 生成功能。

## 数据

| 项目 | 数值 |
|------|------|
| A    | 100  |
| B    | 200  |

## 结论

测试通过。
"""
    result = generate_markdown(content, "test_report")
    print(f"generate_markdown 结果: {result}")

    # 验证文件落盘
    from api.context import get_session_dir
    session_dir = get_session_dir()
    if session_dir:
        files = list(session_dir.glob("*.md"))
        print(f"目录下 .md 文件: {[f.name for f in files]}")
        for f in files:
            print(f"  {f.name} 大小: {f.stat().st_size} 字节")
