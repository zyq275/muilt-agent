"""
ContextVar 会话隔离 — TDD §4.1 / ADR-4

用 ContextVar 保存会话目录与线程标识，任务生命周期内 set/get/reset。

为什么用 ContextVar 而不是全局变量 / threading.local：
  - 每个 asyncio Task（`asyncio.create_task`）在创建时复制一份 context，
    因此并发任务之间互不干扰，天然实现"会话隔离"；
  - threading.local 在 asyncio 下失效（同一线程跑多个任务），全局变量会串号。

使用：
    from api.context import set_session, get_session_dir, get_thread_id, reset_session

    set_session("thread-123")
    work_dir = get_session_dir()   # → output/session_thread-123
    reset_session()
"""

import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Iterator

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from utils.logger_utils import get_logger
from utils.path_utils import OUTPUT_DIR, resolve_output_dir, resolve_upload_dir

logger = get_logger(__name__)

# ── ContextVar 声明 ────────────────────────────────────
_thread_id_var: ContextVar[str | None] = ContextVar("thread_id", default=None)
_session_dir_var: ContextVar[Path | None] = ContextVar("session_dir", default=None)

# 保存 set 时返回的 Token，供 reset 精确回滚到 set 之前的状态
_thread_id_token: ContextVar[Token | None] = ContextVar("thread_id_token", default=None)
_session_dir_token: ContextVar[Token | None] = ContextVar("session_dir_token", default=None)


# ── thread_id 生成 ─────────────────────────────────────

def new_thread_id() -> str:
    """
    生成一个新的会话标识。

    用 uuid4 的十六进制（32 位、无连字符），保证可安全用于目录名。

    Returns:
        新的 thread_id 字符串。
    """
    tid = uuid.uuid4().hex
    logger.debug(f"生成新 thread_id：{tid}")
    return tid


# ── set / get / reset ──────────────────────────────────

def set_session(thread_id: str) -> Path:
    """
    设置当前会话上下文：thread_id 和对应的工作目录。

    工作目录不存在时自动创建（output/session_{thread_id}）。
    同时创建对应的上传目录（upload/session_{thread_id}）。

    Args:
        thread_id: 会话标识。

    Returns:
        会话工作目录的绝对 Path。
    """
    session_dir = resolve_output_dir(thread_id)
    # 上传目录一并就绪，供 /api/upload 落盘
    resolve_upload_dir(thread_id)

    _thread_id_token.set(_thread_id_var.set(thread_id))
    _session_dir_token.set(_session_dir_var.set(session_dir))

    logger.debug(f"会话上下文已设置：thread_id={thread_id}, dir={session_dir}")
    return session_dir


def get_thread_id() -> str | None:
    """获取当前会话的 thread_id；未设置时返回 None。"""
    return _thread_id_var.get()


def get_session_dir() -> Path | None:
    """
    获取当前会话的工作目录；未设置时返回 None。

    注：不自动创建目录——目录由 set_session 负责创建，
        此处只读取，避免在只读场景下产生副作用。
    """
    return _session_dir_var.get()


def get_session_dir_or_default() -> Path:
    """
    获取当前会话工作目录；未设置时回退到 output/default。

    供在会话上下文之外（如 CLI 直接调用工具）也能安全落盘。

    Returns:
        会话目录或 output/default 的绝对 Path（均已确保存在）。
    """
    session_dir = _session_dir_var.get()
    if session_dir is not None:
        return session_dir

    fallback = OUTPUT_DIR / "default"
    fallback.mkdir(parents=True, exist_ok=True)
    logger.debug(f"会话上下文未设置，回退到默认目录：{fallback}")
    return fallback


def reset_session() -> None:
    """
    重置当前会话上下文，回到 set_session 之前的状态。

    优先用 Token 精确回滚（支持嵌套）；若本层没有 Token（例如 set 发生在
    另一个 context 中），退化为置空。
    """
    tid_token = _thread_id_token.get()
    dir_token = _session_dir_token.get()

    if tid_token is not None:
        _thread_id_var.reset(tid_token)
        _thread_id_token.set(None)
    else:
        _thread_id_var.set(None)

    if dir_token is not None:
        _session_dir_var.reset(dir_token)
        _session_dir_token.set(None)
    else:
        _session_dir_var.set(None)

    logger.debug("会话上下文已重置")


@contextmanager
def session_scope(thread_id: str) -> Iterator[Path]:
    """
    会话上下文的上下文管理器，退出时自动重置。

    供 CLI / 测试使用：
        with session_scope("cli-test") as work_dir:
            ...
    """
    try:
        yield set_session(thread_id)
    finally:
        reset_session()


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    import asyncio

    print("=== 1. 基础 set/get/reset ===")
    print(f"初始 thread_id: {get_thread_id()!r}, session_dir: {get_session_dir()!r}")
    tid = new_thread_id()
    work_dir = set_session(tid)
    print(f"set 后 thread_id: {get_thread_id()!r}")
    print(f"set 后 session_dir: {get_session_dir()}")
    print(f"目录存在: {work_dir.exists()}")
    reset_session()
    print(f"reset 后 thread_id: {get_thread_id()!r}")

    print("\n=== 2. session_scope 自动重置 ===")
    with session_scope("cli-check") as d:
        print(f"scope 内: {get_thread_id()!r} → {d.name}")
    print(f"scope 外: {get_thread_id()!r}")

    print("\n=== 3. 并发隔离（两个 asyncio 任务互不串号）===")

    async def worker(name: str) -> str:
        set_session(name)
        await asyncio.sleep(0.05)          # 制造交错
        seen = get_thread_id()
        reset_session()
        return f"{name} 看到的是 {seen}"

    async def main() -> None:
        results = await asyncio.gather(worker("task-A"), worker("task-B"), worker("task-C"))
        for r in results:
            print(f"  {r}")

    asyncio.run(main())

    print("\n=== 4. 未设置上下文时的默认目录 ===")
    print(f"get_session_dir_or_default() → {get_session_dir_or_default()}")
