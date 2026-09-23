"""
全网检索工具 — TDD §7.1

签名（照抄 TDD §7.1）：
    internet_search(
        query: str,
        max_results: int = 5,
        topic: Literal["general", "news", "finance"] = "general",
        include_raw_content: bool = False,
    ) -> str

行为：调用 Tavily 搜索，返回结构化检索结果。
归属：网络检索子智能体。
"""

import os
import sys
from pathlib import Path
from typing import Literal

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tavily import TavilyClient
from dotenv import load_dotenv

from utils.logger_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> str:
    """
    调用 Tavily 执行全网检索，返回结构化结果。

    Args:
        query: 搜索关键词/问题。
        max_results: 返回结果数量，默认 5。
        topic: 搜索类型——general（通用）、news（新闻）、finance（财经）。
        include_raw_content: 是否包含原文内容，默认 False。

    Returns:
        格式化检索结果文本；每条结果含标题、URL、内容摘要；
        失败时返回 "网络检索失败：{错误}"。
    """
    if not TAVILY_API_KEY:
        return "网络检索失败：TAVILY_API_KEY 未设置"

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=include_raw_content,
        )

        results = response.get("results", [])
        if not results:
            return f"未检索到与「{query}」相关的结果"

        lines = []
        for i, item in enumerate(results, 1):
            title = item.get("title", "无标题")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"[{i}] {title}")
            lines.append(f"    URL: {url}")
            lines.append(f"    摘要: {content}")
            if include_raw_content and item.get("raw_content"):
                lines.append(f"    原文: {item['raw_content'][:500]}...")

        result_text = "\n".join(lines)
        logger.info(f"internet_search('{query}') 返回 {len(results)} 条结果")
        return result_text

    except Exception as e:
        logger.error(f"internet_search 失败：{e}")
        return f"网络检索失败：{e}"


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("=== internet_search 测试 ===")
    result = internet_search("阿莫西林的用途", max_results=3)
    print(result)
