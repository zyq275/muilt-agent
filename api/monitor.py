"""
事件上报与连接管理 — TDD §4.1 / §8.2

两个角色：
  - ConnectionManager：按 thread_id 管理 WebSocket 连接，实现会话级事件路由；
  - ToolMonitor：单例，把编排层上报的事件包装成 TDD §8.2 的统一结构后推送。

推送消息统一结构（TDD §8.2，逐字对应）：
    {
      "type": "monitor_event",
      "event": "<事件类型>",
      "message": "<人类可读描述>",
      "data": {},
      "timestamp": "ISO8601"
    }

事件类型：
    session_created  会话工作目录创建后     {"path": "<绝对路径>"}
    tool_start       主智能体调用普通工具   {"tool_name": "...", "args": {...}}
    assistant_call   主智能体调用子智能体   {"assistant_name": "<subagent_type>", "args": "<任务描述>"}
    task_result      返回最终结果           {"result": "<文本>"}
    error            执行失败               （message 为错误说明）
    cancelled        任务被用户终止         {}（TDD §8.2 五种之外的扩展，见下）

关于 cancelled：
    TDD §8.2 只定义了上面五种事件。用户主动终止任务需要一个语义明确的信号——
    复用 error 会让"用户点了终止"在界面上显示成红色报错，与事实不符；复用
    task_result 又会把半截报告当成正常产出。因此新增第六种。它是**纯增量**的：
    不认识这个类型的老消费者按未知事件忽略即可，不会破坏既有契约。

线程模型（关键）：
    编排层 `run_deep_agent` 在 asyncio 任务里运行，其 `on_event` 回调可能从
    **工作线程**（langgraph 内部把同步 LLM 调用丢进线程池）触发。
    而 `WebSocket.send_json` 只能在事件循环线程里调用。
    因此 `ToolMonitor.emit()` 必须线程安全地跨线程投递：
      - 若已在目标事件循环内 → 直接 create_task；
      - 若在其他线程        → asyncio.run_coroutine_threadsafe 投递。
"""

import asyncio
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from utils.logger_utils import get_logger

logger = get_logger(__name__)

# 事件类型常量（供编排层与接入层共用，避免拼写漂移）
EVENT_SESSION_CREATED = "session_created"
EVENT_TOOL_START = "tool_start"
EVENT_ASSISTANT_CALL = "assistant_call"
EVENT_TASK_RESULT = "task_result"
EVENT_ERROR = "error"
# TDD §8.2 之外的扩展事件：用户主动终止任务（详见模块 docstring）
EVENT_CANCELLED = "cancelled"

MESSAGE_TYPE = "monitor_event"


def build_event(event: str, message: str, data: dict | None = None) -> dict:
    """
    构造 TDD §8.2 的统一推送结构。

    Args:
        event: 事件类型（如 tool_start）。
        message: 人类可读描述。
        data: 事件附加数据，缺省为空字典。

    Returns:
        可直接 JSON 序列化的推送字典。
    """
    return {
        "type": MESSAGE_TYPE,
        "event": event,
        "message": message,
        "data": data if data is not None else {},
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# ── 连接管理 ───────────────────────────────────────────

class ConnectionManager:
    """
    按 thread_id 管理 WebSocket 连接（TDD §4.1）。

    一个 thread_id 允许有多个连接（例如用户开了多个页面）。
    集合的增删用锁保护：connect/disconnect 可能来自不同任务。
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[Any]] = {}
        self._lock = threading.Lock()

    async def connect(self, thread_id: str, websocket: Any) -> None:
        """接受连接并登记到指定会话。"""
        await websocket.accept()
        with self._lock:
            self._connections.setdefault(thread_id, set()).add(websocket)
        logger.info(
            f"WebSocket 已连接：thread_id={thread_id}，"
            f"当前连接数={self.connection_count(thread_id)}"
        )

    def disconnect(self, thread_id: str, websocket: Any) -> None:
        """注销连接；该会话无连接时清理键。"""
        with self._lock:
            conns = self._connections.get(thread_id)
            if not conns:
                return
            conns.discard(websocket)
            if not conns:
                self._connections.pop(thread_id, None)
                logger.info(f"WebSocket 已断开：thread_id={thread_id}，该会话无剩余连接")
            else:
                logger.info(
                    f"WebSocket 已断开：thread_id={thread_id}，"
                    f"剩余连接数={len(conns)}"
                )

    def connection_count(self, thread_id: str) -> int:
        """返回指定会话的活跃连接数。"""
        with self._lock:
            return len(self._connections.get(thread_id, ()))

    def active_threads(self) -> list[str]:
        """返回当前有活跃连接的 thread_id 列表。"""
        with self._lock:
            return list(self._connections.keys())

    def _targets(self, thread_id: str) -> list[Any]:
        """取指定会话当前连接快照（持锁拷贝，避免推送时长时间持锁）。"""
        with self._lock:
            return list(self._connections.get(thread_id, ()))

    async def broadcast(self, thread_id: str, payload: dict) -> None:
        """
        向指定会话的全部连接推送一条消息。

        必须在事件循环线程中调用（WebSocket.send_json 非线程安全）。
        单个连接发送失败即视为死连接，回收掉。

        Args:
            thread_id: 目标会话。
            payload: 已构造好的推送字典。
        """
        targets = self._targets(thread_id)
        if not targets:
            logger.debug(f"会话 {thread_id} 无活跃连接，事件仅记录不入队")
            return

        dead: list[Any] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.warning(f"向会话 {thread_id} 推送失败，回收连接：{e}")
                dead.append(ws)

        for ws in dead:
            self.disconnect(thread_id, ws)


# ── 事件上报单例 ───────────────────────────────────────

class ToolMonitor:
    """
    事件上报单例（TDD §4.1）。

    持有 ConnectionManager，并记录 uvicorn 的事件循环，以便从工作线程
    安全地把事件投递回事件循环。
    """

    _instance: "ToolMonitor | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "ToolMonitor":
        # 单例：全局只有一个事件总线
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        # __new__ 保证只进来一次
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.manager = ConnectionManager()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_lock = threading.Lock()

    # ── 事件循环绑定 ───────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """
        绑定（或更新）事件循环。

        接入层在每个请求/连接入口调用；不传则取当前运行的循环。
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("bind_loop 时无运行中的事件循环，跳过绑定")
                return
        with self._loop_lock:
            if self._loop is not loop:
                self._loop = loop
                logger.debug("ToolMonitor 已绑定事件循环")

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._loop_lock:
            return self._loop

    # ── 事件构造与投递 ─────────────────────────────────

    def emit(self, thread_id: str, event: str, message: str, data: dict | None = None) -> dict:
        """
        上报一个事件（线程安全）。

        从任意线程调用均可：内部判断当前是否已在目标事件循环内，
        决定直接调度还是跨线程投递。

        Args:
            thread_id: 目标会话，事件只推给该会话的连接。
            event: 事件类型。
            message: 人类可读描述。
            data: 事件附加数据。

        Returns:
            构造出的推送字典（便于直接复用 result 字段）。
        """
        payload = build_event(event, message, data)
        logger.debug(f"[事件] {thread_id} | {event} | {message}")

        loop = self._get_loop()
        if loop is None or loop.is_closed():
            # 无接入层消费者（例如 CLI 直接跑 run_deep_agent），静默丢弃
            logger.debug("未绑定事件循环，事件不入队（CLI 模式属正常）")
            return payload

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        coro = self.manager.broadcast(thread_id, payload)
        if running_loop is loop:
            # 已在事件循环线程内：直接调度
            loop.create_task(coro)
        else:
            # 来自工作线程：跨线程投递（非阻塞）
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError as e:
                logger.warning(f"跨线程投递事件失败：{e}")
        return payload

    def callback(self, thread_id: str) -> Callable[[str, str, dict], None]:
        """
        生成绑定了 thread_id 的 on_event 回调，直接交给 run_deep_agent 使用。

        run_deep_agent 的约定是 on_event(event_type, message, data)。
        """
        def _on_event(event: str, message: str, data: dict | None = None) -> None:
            self.emit(thread_id, event, message, data)

        return _on_event

    async def broadcast_now(self, thread_id: str, event: str, message: str,
                            data: dict | None = None) -> None:
        """在事件循环内直接等待推送完成（供接入层自身使用）。"""
        await self.manager.broadcast(thread_id, build_event(event, message, data))


# 模块级单例
_monitor = ToolMonitor()


def get_monitor() -> ToolMonitor:
    """获取全局 ToolMonitor 单例。"""
    return _monitor


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("=== 1. 单例校验 ===")
    print(f"get_monitor() is ToolMonitor() : {get_monitor() is ToolMonitor()}")
    print(f"两次 get_monitor() 相同           : {get_monitor() is get_monitor()}")

    print("\n=== 2. 事件结构（TDD §8.2）===")
    import json
    evt = build_event(EVENT_TOOL_START, "主智能体调用工具", {"tool_name": "generate_markdown", "args": {"filename": "x.md"}})
    print(json.dumps(evt, ensure_ascii=False, indent=2))

    print("\n=== 3. 未绑定循环时 emit 不崩 ===")
    m = get_monitor()
    m.emit("t1", EVENT_SESSION_CREATED, "会话目录已创建", {"path": "C:/x"})
    print("emit 已安全返回（无消费者）")

    print("\n=== 4. 伪造连接：按会话路由 + 跨线程投递 ===")

    class FakeWS:
        def __init__(self, name: str) -> None:
            self.name = name
            self.received: list[dict] = []

        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            self.received.append(payload)

    async def main() -> None:
        mon = get_monitor()
        mon.bind_loop()
        ws_a = FakeWS("A")
        ws_b = FakeWS("B")
        await mon.manager.connect("thread-A", ws_a)
        await mon.manager.connect("thread-B", ws_b)

        # 事件循环内 emit
        mon.emit("thread-A", EVENT_TOOL_START, "循环内事件", {"tool_name": "t"})
        # 工作线程 emit（验证跨线程投递）
        threading.Thread(
            target=mon.emit,
            args=("thread-A", EVENT_ASSISTANT_CALL, "工作线程事件",
                  {"assistant_name": "数据库查询助手", "args": "查询药品"}),
        ).start()

        await asyncio.sleep(0.3)   # 等投递完成
        print(f"A 收到 {len(ws_a.received)} 条（应为 2）")
        for r in ws_a.received:
            print(f"   - {r['event']}: {r['message']}")
        print(f"B 收到 {len(ws_b.received)} 条（应为 0，会话隔离）")
        print(f"活跃会话: {mon.manager.active_threads()}")

        mon.manager.disconnect("thread-A", ws_a)
        print(f"断开 A 后活跃会话: {mon.manager.active_threads()}")

    asyncio.run(main())

    print("\n=== 5. 回调适配 run_deep_agent 的 on_event 约定 ===")
    cb = get_monitor().callback("thread-X")
    cb(EVENT_ERROR, "执行失败：演示", {})
    print(f"callback 可调用: {callable(cb)}（无循环时静默丢弃，不抛异常）")
