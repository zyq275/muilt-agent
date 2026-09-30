"""
探渊 · 多智能体数据分析系统 — 程序入口

启动 FastAPI 应用（REST + WebSocket），默认监听 127.0.0.1:8000。

注：TDD §16.2 写的是 0.0.0.0（监听所有网卡），实际改成 127.0.0.1（仅本机）。
本系统持有数据库只读凭据、且前端开发期靠 vite 代理同源访问，没有任何需要从
局域网访问的场景，绑回环地址可以少一个暴露面。要改成对外监听，传给
start_server(host=...) 即可。

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
