"""
全局彩色日志工具 — colorlog

格式：时间 | 文件:行号 | 级别 | 消息
示例：2026-09-22 10:30:00 | main.py:12 | INFO | 服务已启动

使用：
    from utils.logger_utils import get_logger
    logger = get_logger(__name__)
    logger.info("服务已启动")
"""

import logging
import sys

import colorlog


_DEFAULT_FORMAT = "%(log_color)s%(asctime)s | %(filename)s:%(lineno)d | %(levelname)-8s | %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 全局标志：避免重复添加 handler
_initialized = False


def _init_root_logger() -> None:
    """初始化根 logger，仅执行一次。"""
    global _initialized
    if _initialized:
        return

    root = logging.getLogger()
    if root.handlers:
        # 已有 handler（可能是第三方库添加的），跳过
        _initialized = True
        return

    handler = colorlog.StreamHandler(sys.stdout)
    handler.setFormatter(
        colorlog.ColoredFormatter(
            fmt=_DEFAULT_FORMAT,
            datefmt=_DEFAULT_DATE_FORMAT,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _initialized = True


def get_logger(name: str, level: int | None = None) -> logging.Logger:
    """
    获取命名 logger。

    Args:
        name: 通常是 __name__，用于标识日志来源模块。
        level: 可选，覆盖默认级别。

    Returns:
        配置好 colorlog handler 的 logging.Logger 实例。
    """
    _init_root_logger()
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger
