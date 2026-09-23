"""
网络检索子智能体配置 — TDD §4.2 / §6.2

配置取自 load_prompt.sub_agents_config["web_search"]，
工具：internet_search（TDD §7.1）。
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langchain_core.tools import tool

from agent.load_prompt import apply_security_boundary, sub_agents_config
from tools.internet_search_tool import internet_search
from utils.logger_utils import get_logger

logger = get_logger(__name__)

# 数据源标识（H4 校验用）
SOURCE_KEY = "web_search"


def get_web_search_subagent() -> dict:
    """
    构造网络检索子智能体声明。

    Returns:
        SubAgent 字典：{name, description, system_prompt, tools}
    """
    cfg = sub_agents_config[SOURCE_KEY]
    return {
        "name": cfg["name"],
        "description": cfg["description"],
        # 网页内容是不可信输入，追加安全边界（TDD §14 Prompt 注入）
        "system_prompt": apply_security_boundary(cfg["system_prompt"], subagent=True),
        "tools": [tool(internet_search)],
    }


if __name__ == "__main__":
    sa = get_web_search_subagent()
    print(f"name: {sa['name']}")
    print(f"description: {sa['description'][:60]}...")
    print(f"tools: {[t.name for t in sa['tools']]}")
