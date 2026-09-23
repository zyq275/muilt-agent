"""
探渊 · 多智能体数据分析系统 — 程序入口

启动 FastAPI 应用（REST + WebSocket），监听 0.0.0.0:8000。
应用本体定义在 api/server.py（TDD §4.1 接入层），此处只负责加载环境变量与启动。
"""

from dotenv import load_dotenv

from utils.logger_utils import get_logger

# 加载 .env 环境变量
load_dotenv()

logger = get_logger(__name__)

# 延迟到 load_dotenv 之后再导入应用，确保路由注册时环境变量已就绪
from api.server import app, start_server  # noqa: E402


def main() -> None:
    """入口函数，供 pyproject.toml [project.scripts] 调用。"""
    logger.info("探渊 启动")
    start_server()


if __name__ == "__main__":
    main()
