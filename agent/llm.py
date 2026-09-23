"""
LLM 初始化模块 — TDD §4.2

用 langchain-openai 的 init_chat_model 初始化 LLM 实例。
模型/端点/密钥从环境变量读取（TDD §2、§12）。

使用：
    from agent.llm import get_llm
    llm = get_llm()
    response = await llm.ainvoke("你好")
"""

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

from utils.logger_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

# ── 环境变量 ──────────────────────────────────────────
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")


def get_llm(
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    timeout: int = 30,
    **kwargs,
) -> ChatOpenAI:
    """
    初始化并返回 LLM 实例（OpenAI 兼容协议）。

    Args:
        model: 模型名，默认读 DEEPSEEK_MODEL 环境变量。
        base_url: API 端点，默认读 DEEPSEEK_BASE_URL 环境变量。
        api_key: API 密钥，默认读 DEEPSEEK_API_KEY 环境变量。
        temperature: 生成温度，默认 0.0（确定性输出）。
        timeout: 请求超时秒数，默认 30。
        **kwargs: 其他传递给 ChatOpenAI 的参数。

    Returns:
        ChatOpenAI 实例。
    """
    _model = model or DEEPSEEK_MODEL
    _base_url = base_url or DEEPSEEK_BASE_URL
    _api_key = api_key or DEEPSEEK_API_KEY

    if not _api_key:
        logger.warning("DEEPSEEK_API_KEY 未设置，LLM 调用将失败")

    llm = ChatOpenAI(
        model=_model,
        base_url=_base_url,
        api_key=_api_key,
        temperature=temperature,
        request_timeout=timeout,
        **kwargs,
    )

    logger.info(f"LLM 已初始化：model={_model}, base_url={_base_url}")
    return llm


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    llm = get_llm()
    print(f"LLM 实例：{llm}")
    print(f"  model: {llm.model_name}")
    print(f"  base_url: {llm.openai_api_base}")

    # 最小调用测试
    import asyncio

    async def _test():
        try:
            resp = await llm.ainvoke("用一句话回答：1+1等于几？")
            print(f"  test response: {resp.content}")
        except Exception as e:
            print(f"  test failed: {e}")

    asyncio.run(_test())
