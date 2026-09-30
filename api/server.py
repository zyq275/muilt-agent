"""
接入层 · 服务 — TDD §4.1 / §8.1 / §8.2

职责：
  - REST 路由：任务提交 / 文件上传 / 文件浏览 / 文件下载；
  - WebSocket 端点：心跳 + 会话级事件推送；
  - 越界校验：/api/files 与 /api/download 的目标路径必须落在结果目录内。

接口一览（TDD §8.1）：
    POST /api/task      {"query": str, "thread_id": str|null}
                        → {"status":"started","thread_id":str}
    POST /api/upload    multipart: files[] + thread_id
                        → {"status":"uploaded","files":[str]}
    POST /api/cancel    {"thread_id": str}
                        → {"status":"cancelled"|"not_running", "thread_id": str}
    GET  /api/files     ?path=<绝对路径>
                        → {"files":[{name,type,path,size,mtime}]}（mtime 倒序）
    GET  /api/download  ?path=<绝对路径>
                        → 文件流 / {"error": ...}
    WS   /ws/{thread_id}
                        → 收到文本回 {"type":"pong","message":"服务端已收到: <原文>"}
                        → 编排层事件按 §8.2 结构推送
"""

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from api.context import new_thread_id
from api.monitor import EVENT_CANCELLED, EVENT_ERROR, get_monitor
from utils.logger_utils import get_logger
from utils.path_utils import (
    OUTPUT_DIR,
    UPLOAD_DIR,
    resolve_upload_dir,
    sanitize_path,
    validate_within_root,
)

load_dotenv()
logger = get_logger(__name__)

# 上传分块大小：1 MiB（流式写盘，避免大文件一次性读入内存）
CHUNK_SIZE = 1024 * 1024

# 服务端心跳间隔（秒）
HEARTBEAT_INTERVAL = 30

# thread_id 白名单：字母/数字/下划线/连字符，长度 1–64
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ── 工具函数 ───────────────────────────────────────────

def _safe_thread_id(raw: str) -> str:
    """
    校验 thread_id 合法性。

    不用 sanitize_path 做静默改写——那会让"上传到某个目录"和"任务写到另一个
    目录"悄然错位。非法直接拒绝，行为可预期。

    Args:
        raw: 客户端传入的 thread_id。

    Returns:
        校验通过的 thread_id。

    Raises:
        ValueError: 格式非法。
    """
    tid = (raw or "").strip()
    if not _THREAD_ID_RE.match(tid):
        raise ValueError(
            f"非法 thread_id：{raw!r}（只允许字母/数字/下划线/连字符，长度 1–64）"
        )
    return tid


def _resolve_within_output(raw_path: str) -> Path:
    """
    解析并校验路径位于结果目录内（TDD §8.1 越界校验）。

    Args:
        raw_path: 客户端传入的绝对路径；空值表示结果目录根。

    Returns:
        校验通过的绝对路径。

    Raises:
        HTTPException: 403，路径越界。
    """
    raw = (raw_path or "").strip()
    if not raw:
        return OUTPUT_DIR.resolve()

    try:
        return validate_within_root(Path(raw), OUTPUT_DIR)
    except ValueError as e:
        logger.warning(f"越界访问被拒绝：{raw}（{e}）")
        raise HTTPException(status_code=403, detail="拒绝访问：路径越界")


# ── 在跑任务登记表（供取消用）──────────────────────────
#
# create_task 用 asyncio.create_task 起后台任务后，原本**没有留下任何句柄**，
# 因此没有办法取消它。这里把 task 按 thread_id 存下来，POST /api/cancel 才能
# 找到并取消对应的执行。
#
# 只有一个事件循环（uvicorn 主循环）会写这张表，所以用普通 dict 即可；
# 句柄在任务结束时由 done_callback 主动摘除，不会长期堆积。
_running_tasks: dict[str, asyncio.Task] = {}


# ── 应用与路由 ─────────────────────────────────────────

def create_app() -> FastAPI:
    """构造并返回 FastAPI 应用。"""
    app = FastAPI(
        title="探渊 · 多智能体数据分析系统",
        version="0.1.0",
        description="企业智能数据分析：多智能体编排 + 三路检索 + 反思循环 + 报告生成",
    )

    # 供前端开发服务器（Vite，默认 5173）跨域访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_routes(app)
    return app


class TaskRequest(BaseModel):
    """POST /api/task 请求体。"""

    query: str = Field(..., min_length=1, description="用户任务描述")
    thread_id: str | None = Field(default=None, description="会话标识，缺省则新建")


class CancelRequest(BaseModel):
    """POST /api/cancel 请求体。"""

    thread_id: str = Field(..., description="要终止的会话标识")


def _register_routes(app: FastAPI) -> None:

    @app.get("/")
    async def root():
        """健康检查端点。"""
        return {"status": "ok", "service": "探渊"}

    # ── 任务提交 ───────────────────────────────────────

    @app.post("/api/task")
    async def create_task(req: TaskRequest):
        """
        提交任务：异步后台执行，立即返回 thread_id（TDD §8.1）。

        用 asyncio.create_task 起后台任务，请求不被长耗时的智能体编排阻塞。
        """
        # 未传 thread_id 则新建
        if req.thread_id and req.thread_id.strip():
            try:
                thread_id = _safe_thread_id(req.thread_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            thread_id = new_thread_id()

        query = req.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="query 不能为空")

        # 绑定事件循环，让工作线程上报的事件能推回本循环
        get_monitor().bind_loop()

        logger.info(f"收到任务：thread_id={thread_id}, query={query[:50]}")
        # 立即返回，不 await 执行结果。句柄登记到表里，供 /api/cancel 取消。
        task = asyncio.create_task(_execute_task(query, thread_id))
        _running_tasks[thread_id] = task
        # 任务无论正常结束、异常还是被取消，都要把句柄摘掉，否则表会一直涨
        task.add_done_callback(lambda _t, tid=thread_id: _running_tasks.pop(tid, None))

        return {"status": "started", "thread_id": thread_id}

    # ── 任务取消 ───────────────────────────────────────

    @app.post("/api/cancel")
    async def cancel_task(req: CancelRequest):
        """
        终止某会话正在执行的任务。

        注意这里取消的是**当前进程内、由本接口所在的事件循环持有的那个
        asyncio.Task**。具体含义：
          - run_deep_agent 是 async 函数，取消会在它的下一个 await 点抛出
            CancelledError 并一路向上传播（agent/main_agent.py 里的
            `except Exception` 抓不到它——CancelledError 继承自 BaseException，
            所以不会被误当成"执行失败"），其 finally 仍会执行 reset_session()；
          - 已经发出去的同步调用（线程池里的 LLM / 数据库请求）不会被强行中断，
            它们跑完的结果会被丢弃。所以取消是"尽快停"，不是"立刻断"；
          - 已经落盘的中间产物（.md/.pdf）保持原样，不会回滚。
        """
        try:
            thread_id = _safe_thread_id(req.thread_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        task = _running_tasks.get(thread_id)
        if task is None or task.done():
            # 任务已经结束（或从未在本进程跑过）：如实回报，不假装成功
            return {"status": "not_running", "thread_id": thread_id}

        task.cancel()
        logger.info(f"任务已请求取消：thread_id={thread_id}")

        # 在事件循环内直接推送，确保前端一定能收到终止信号
        await get_monitor().broadcast_now(
            thread_id, EVENT_CANCELLED, "任务已被用户终止", {}
        )
        return {"status": "cancelled", "thread_id": thread_id}

    # ── 文件上传 ───────────────────────────────────────

    @app.post("/api/upload")
    async def upload_files(
        thread_id: str = Form(..., description="会话标识"),
        files: list[UploadFile] = File(..., description="待上传文件"),
    ):
        """
        上传文件：流式写入 upload/session_{thread_id}（TDD §8.1）。
        """
        try:
            tid = _safe_thread_id(thread_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not files:
            raise HTTPException(status_code=400, detail="未收到任何文件")

        target_dir = resolve_upload_dir(tid)
        saved: list[str] = []

        for upload in files:
            # 先取 basename 去掉目录部分，再清洗；防止 ../../evil 之类
            raw_name = Path(upload.filename or "").name
            safe_name = sanitize_path(raw_name).strip()
            if not safe_name:
                safe_name = f"upload_{len(saved) + 1}.bin"

            dest = target_dir / safe_name
            # 双保险：清洗后仍须落在上传目录内
            try:
                dest = validate_within_root(dest, UPLOAD_DIR)
            except ValueError:
                logger.warning(f"上传文件名越界被拒绝：{upload.filename!r}")
                raise HTTPException(status_code=403, detail="拒绝访问：文件名非法")

            try:
                written = 0
                with dest.open("wb") as out:
                    while True:
                        chunk = await upload.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                        written += len(chunk)
                logger.info(f"已保存上传文件：{dest}（{written} 字节）")
                saved.append(safe_name)
            except Exception as e:
                logger.error(f"写入上传文件失败：{dest}（{e}）")
                raise HTTPException(status_code=500, detail=f"文件写入失败：{e}")
            finally:
                await upload.close()

        return {"status": "uploaded", "files": saved}

    # ── 文件浏览 ───────────────────────────────────────

    @app.get("/api/files")
    async def list_files(path: str = Query(default="", description="结果目录内的绝对路径")):
        """
        列出目录内容，mtime 倒序（TDD §8.1）。
        """
        target = _resolve_within_output(path)

        if not target.exists():
            return {"files": []}

        entries = [target] if target.is_file() else list(target.iterdir())

        files = []
        for p in entries:
            try:
                st = p.stat()
            except OSError as e:
                logger.warning(f"跳过无法访问的条目 {p}：{e}")
                continue
            is_dir = p.is_dir()
            files.append({
                "name": p.name,
                "type": "dir" if is_dir else "file",
                "path": str(p),
                "size": 0 if is_dir else st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime)
                                  .astimezone()
                                  .isoformat(timespec="seconds"),
            })

        files.sort(key=lambda x: x["mtime"], reverse=True)
        return {"files": files}

    # ── 文件下载 ───────────────────────────────────────

    @app.get("/api/download")
    async def download_file(path: str = Query(..., description="结果目录内的绝对路径")):
        """
        下载文件（TDD §8.1）：文件流，或 {"error": ...}。
        """
        try:
            target = validate_within_root(Path(path), OUTPUT_DIR)
        except ValueError as e:
            logger.warning(f"下载越界被拒绝：{path}（{e}）")
            return JSONResponse(status_code=403, content={"error": "拒绝访问：路径越界"})

        if not target.exists():
            return JSONResponse(status_code=404, content={"error": f"文件不存在：{target}"})
        if not target.is_file():
            return JSONResponse(status_code=400, content={"error": f"目标不是文件：{target}"})

        return FileResponse(
            path=str(target),
            filename=target.name,
            media_type="application/octet-stream",
        )

    # ── WebSocket ──────────────────────────────────────

    @app.websocket("/ws/{thread_id}")
    async def websocket_endpoint(websocket: WebSocket, thread_id: str):
        """
        WebSocket 端点（TDD §8.2）：心跳 + 会话级事件推送。
        """
        try:
            tid = _safe_thread_id(thread_id)
        except ValueError as e:
            logger.warning(f"WebSocket 拒绝连接：{e}")
            await websocket.close(code=1008, reason="非法 thread_id")
            return

        monitor = get_monitor()
        monitor.bind_loop()
        manager = monitor.manager

        await manager.connect(tid, websocket)
        heartbeat = asyncio.create_task(_heartbeat_loop(websocket, tid))

        try:
            while True:
                text = await websocket.receive_text()
                # TDD §8.2 心跳：收到文本 → 回 pong
                await websocket.send_json({
                    "type": "pong",
                    "message": f"服务端已收到: {text}",
                })
        except WebSocketDisconnect:
            logger.info(f"WebSocket 客户端主动断开：thread_id={tid}")
        except Exception as e:
            logger.warning(f"WebSocket 异常：thread_id={tid}（{e}）")
        finally:
            heartbeat.cancel()
            manager.disconnect(tid, websocket)

    async def _heartbeat_loop(websocket: WebSocket, thread_id: str) -> None:
        """周期性下发服务端心跳，探测并清理死连接。"""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await websocket.send_json({
                    "type": "ping",
                    "message": "服务端心跳",
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"心跳终止：thread_id={thread_id}（{e}）")


# ── 后台任务执行 ───────────────────────────────────────

async def _execute_task(query: str, thread_id: str) -> None:
    """
    后台执行 run_deep_agent，并把事件推送到该会话的 WebSocket。

    延迟导入：agent.main_agent 会连带加载 langchain / transformers 等重依赖，
    放在模块顶层会让服务启动白等十几秒，也会让不碰智能体的接口（健康检查、
    纯文件浏览）承担同一份导入代价。
    """
    monitor = get_monitor()
    on_event = monitor.callback(thread_id)

    try:
        from agent.main_agent import run_deep_agent

        logger.info(f"后台任务开始：thread_id={thread_id}")
        report = await run_deep_agent(query, thread_id, on_event=on_event)
        logger.info(f"后台任务结束：thread_id={thread_id}，报告 {len(report)} 字符")
    except Exception as e:
        # run_deep_agent 内部已兜底，这里防的是导入失败等更外层异常
        logger.exception(f"后台任务异常：thread_id={thread_id}")
        monitor.emit(thread_id, EVENT_ERROR, f"执行失败：{e}", {})


# 模块级应用实例（uvicorn 目标）
app = create_app()


# ── 启动入口 ───────────────────────────────────────────

def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """启动 uvicorn 服务器。"""
    import uvicorn

    logger.info(f"探渊服务启动中：http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("=== 路由清单 ===")
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", "")
        if methods:
            print(f"  {'/'.join(sorted(methods)):<8} {path}")
        else:
            print(f"  {'WS':<8} {path}")

    print("\n=== _safe_thread_id 校验 ===")
    for case in ["abc-123", "A_b-9", "bad/../id", "含中文", "", "x" * 65]:
        try:
            print(f"  {case!r:<20} → {_safe_thread_id(case)!r}")
        except ValueError as e:
            print(f"  {case!r:<20} → 拒绝（{e}）")

    print("\n=== _resolve_within_output 越界校验 ===")
    from utils.path_utils import PROJECT_ROOT
    for case in ["", str(OUTPUT_DIR), str(OUTPUT_DIR / "session_x"),
                 str(PROJECT_ROOT / "main.py"), r"C:\Windows\System32"]:
        try:
            print(f"  {case!r:<60} → 允许 {_resolve_within_output(case)}")
        except HTTPException as e:
            print(f"  {case!r:<60} → {e.status_code} {e.detail}")

    print("\n=== 提示：完整接口验证请用 python main.py + curl ===")
