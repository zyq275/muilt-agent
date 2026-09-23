"""
数据库查询子智能体配置 — TDD §4.2 / §6.3

配置取自 load_prompt.sub_agents_config["db_query"]，
工具：list_sql_tables / get_table_data / execute_sql_query（TDD §7.2）。
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langchain_core.tools import tool

from agent.load_prompt import sub_agents_config
from tools.mysql_tools import (
    list_sql_tables,
    get_table_data,
    execute_sql_query,
)
from utils.logger_utils import get_logger

logger = get_logger(__name__)

# 数据源标识（H4 校验用）
SOURCE_KEY = "db_query"


def get_db_query_subagent() -> dict:
    """
    构造数据库查询子智能体声明。

    Returns:
        SubAgent 字典：{name, description, system_prompt, tools}
    """
    cfg = sub_agents_config[SOURCE_KEY]
    return {
        "name": cfg["name"],
        "description": cfg["description"],
        "system_prompt": cfg["system_prompt"],
        "tools": [
            tool(list_sql_tables),
            tool(get_table_data),
            tool(execute_sql_query),
        ],
    }


if __name__ == "__main__":
    sa = get_db_query_subagent()
    print(f"name: {sa['name']}")
    print(f"description: {sa['description'][:60]}...")
    print(f"tools: {[t.name for t in sa['tools']]}")
