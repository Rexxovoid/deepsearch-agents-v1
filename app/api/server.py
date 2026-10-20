"""
FastAPI 接口层与项目闭环入口

负责承接前端的任务提交、任务取消、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的 DeepAgents 执行放到后台
任务中；执行进度、工具调用和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
import shutil
import uuid
import datetime
import subprocess
import sys
from contextlib import suppress
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import uvicorn
from fastapi import (
    FastAPI,
    Depends,
    Request,
    Response,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.monitor import manager
from app.task_queue import QueueConflict, QueueSettings, QueueStore
from app.api.auth import current_user, identify, login, COOKIE, connect as auth_connect
from app.api.ownership import ensure_thread, list_threads
import hashlib
import os

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def queue_store():
    return app.state.queue


def spawn_worker():
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen([sys.executable, "-m", "app.worker", "--db", str(manager.store.path)],
                            cwd=Path(__file__).resolve().parents[2], creationflags=flags, env=environment)


async def deliver_queue_events(start_id):
    cursor = start_id
    while True:
        for thread_id, event in manager.store.after(cursor):
            await manager.send_to_thread(event, thread_id)
            cursor = event["id"]
        await asyncio.sleep(0.1)


async def supervise_worker():
    process = None
    try:
        while True:
            if process is None or process.poll() is not None:
                process = spawn_worker()
            await asyncio.sleep(1)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    manager.set_loop(asyncio.get_running_loop())
    _app.state.queue = QueueStore(manager.store)
    _app.state.queue_settings = QueueSettings.from_env()
    # Legacy pre-queue in-flight tasks have no durable input; retain their history.
    for thread_id in manager.store.unfinished_threads():
        if _app.state.queue.latest(thread_id) is None:
            _publish_event(thread_id, "error", "旧版任务已中断，没有队列记录，无法恢复，请重新提交。")
    _app.state.queue.recover_expired(_app.state.queue_settings)
    pump = asyncio.create_task(deliver_queue_events(manager.store.latest_id()))
    supervisor = asyncio.create_task(supervise_worker()) if os.getenv("TASK_QUEUE_AUTOSTART", "1") == "1" else None
    try:
        yield
    finally:
        for task in (supervisor, pump):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


# 当前文件位于 app/api/server.py，运行时目录统一收敛到 app 目录
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

app = FastAPI(title="DeepAgents API", lifespan=lifespan)

# output 保存每个会话最终工作区，前端只允许从这里浏览和下载生成文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# updated 暂存用户上传文件，run_deep_agent 启动时会复制到对应 output/session_xxx
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 教学项目通常前后端分别本地启动，这里放开跨域以便 Vite 页面直接调用 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[value.strip() for value in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def allowed_origin(origin: str | None, base_url: str):
    configured = {value.strip() for value in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")}
    return not origin or origin in configured or origin == base_url.rstrip("/").replace("ws://", "http://").replace("wss://", "https://")


@app.middleware("http")
async def check_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not allowed_origin(request.headers.get("origin"), str(request.base_url)):
        return JSONResponse({"detail": "拒绝来自未授权站点的请求"}, status_code=403)
    return await call_next(request)


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=256)


@app.post("/api/auth/{action}")
async def authenticate(action: str, credentials: Credentials, response: Response):
    if action not in {"login", "register"}:
        raise HTTPException(404)
    token = login(credentials.username, credentials.password, action == "register")
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=os.getenv("COOKIE_SECURE", "0") == "1", max_age=30*86400)
    return {"username": credentials.username.strip()}


@app.get("/api/auth/me")
async def me(user: str = Depends(current_user)):
    with auth_connect() as conn:
        name = conn.execute("SELECT name FROM users WHERE id=?", (user,)).fetchone()[0]
    return {"username": name}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE, "")
    with auth_connect() as conn:
        conn.execute("DELETE FROM logins WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE)
    return {"status": "ok"}


@app.get("/api/conversations")
async def conversations(user: str = Depends(current_user)):
    return list_threads(manager.store, user)


@app.get("/api/conversations/{thread_id}/uploads")
async def uploaded_files(thread_id: str, user: str = Depends(current_user)):
    ensure_thread(manager.store, thread_id, user, create=True)
    folder = (updated_dir / f"session_{thread_id}").resolve()
    if not folder.is_relative_to(updated_dir.resolve()):
        raise HTTPException(403)
    return [{"name": file.name, "size": file.stat().st_size} for file in folder.glob("*")
            if file.is_file() and file.resolve().is_relative_to(folder)]


def check_owned_path(path: str, user: str):
    resolved = Path(path).resolve()
    for root in (output_dir.resolve(), updated_dir.resolve()):
        if resolved.is_relative_to(root):
            parts = resolved.relative_to(root).parts
            if parts and parts[0].startswith("session_"):
                ensure_thread(manager.store, parts[0][8:], user)
                return resolved
    raise HTTPException(403, "无权访问该文件或目录")


class TaskRequest(BaseModel):
    """前端启动任务时提交的请求体。"""

    query: str = Field(min_length=1, max_length=50000)
    thread_id: str | None = None


def _publish_event(thread_id: str, event: str, message: str, data: dict | None = None):
    return manager.publish(thread_id, {
        "type": "monitor_event", "event": event, "message": message,
        "data": data or {}, "timestamp": datetime.datetime.now().isoformat(),
    })


@app.post("/api/task")
async def run_task(request: TaskRequest, user: str = Depends(current_user)):
    thread_id = request.thread_id or str(uuid.uuid4())
    ensure_thread(manager.store, thread_id, user, create=True)
    query = request.query.strip()
    if not query:
        raise HTTPException(400, "请输入研搜任务")
    try:
        job = queue_store().enqueue(thread_id, query, app.state.queue_settings)
    except QueueConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "queued", "thread_id": thread_id, "job_id": job["id"]}


@app.get("/api/task/{thread_id}")
async def task_status(thread_id: str, user: str = Depends(current_user)):
    ensure_thread(manager.store, thread_id, user)
    return queue_store().status(thread_id)


@app.post("/api/task/{thread_id}/retry")
async def retry_task(thread_id: str, user: str = Depends(current_user)):
    ensure_thread(manager.store, thread_id, user)
    previous = queue_store().latest(thread_id)
    if not previous or previous["status"] != "failed":
        raise HTTPException(409, "仅可重新提交已失败的任务")
    try:
        job = queue_store().enqueue(thread_id, previous["query"], app.state.queue_settings, retry_of=previous["id"])
    except QueueConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "queued", "thread_id": thread_id, "job_id": job["id"]}


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str, user: str = Depends(current_user)):
    ensure_thread(manager.store, thread_id, user)
    status = queue_store().cancel(thread_id)
    if status is None:
        raise HTTPException(404, "没有可取消的任务")
    return {"status": status, "thread_id": thread_id}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...), user: str = Depends(current_user)):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    ensure_thread(manager.store, thread_id, user, create=True)
    # 上传文件先按会话隔离保存，避免不同任务读取到彼此的附件
    target_dir = updated_dir / f"session_{thread_id}"
    if not target_dir.resolve().is_relative_to(updated_dir.resolve()):
        raise HTTPException(403, "无权写入该目录")
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for file in files:
        filename = file.filename or ""
        if not filename or filename in {".", ".."} or any(c in filename for c in '/\\:'):
            raise HTTPException(400, "无效的文件名")
        file_path = target_dir / filename
        if not file_path.resolve().is_relative_to(target_dir.resolve()):
            raise HTTPException(403, "无权写入该路径")
        # 直接复制文件流，避免大文件一次性读入内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str, user: str = Depends(current_user)):
    abs_path = check_owned_path(path, user)
    # FileResponse 会以流式响应返回文件内容，并让浏览器使用原文件名下载
    if not abs_path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str, user: str = Depends(current_user)):
    abs_path = check_owned_path(path, user)
    if not abs_path.is_dir():
        raise HTTPException(404, "目录不存在")

    files = []
    try:
        # 递归返回文件元数据，前端据此渲染文件列表并发起下载请求
        for file_path in abs_path.rglob("*"):
            if file_path.is_file() and file_path.resolve().is_relative_to(abs_path):
                stat = file_path.stat()
                files.append(
                    {
                        "name": file_path.name,
                        "type": "file",
                        "path": str(file_path),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 最新生成的文件排在前面，方便用户优先看到本次任务产物
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，ConnectionManager 会用 thread_id 保存 WebSocket。monitor 后续
    发送事件时只需要按 thread_id 查找连接，就能把进度推给对应页面。循环中的
    receive_text 用于接收前端心跳，避免连接空闲断开。
    """
    try:
        if not allowed_origin(websocket.headers.get("origin"), str(websocket.base_url)):
            raise HTTPException(403)
        user = identify(websocket.cookies.get(COOKIE))
        ensure_thread(manager.store, thread_id, user, create=True)
    except HTTPException:
        await websocket.close(code=1008)
        return

    # 连接建立后立即按 thread_id 注册，monitor 后续才能把事件定向推给当前页面
    await manager.connect(websocket, thread_id,
                          lambda: queue_store().status(thread_id)["is_running"],
                          lambda: queue_store().status(thread_id))

    try:
        while True:
            # 前端通常发送 ping 心跳；服务端回复 pong，顺便维持连接活跃
            data = await websocket.receive_text()
            await websocket.send_json(
                {"type": "pong", "message": f"服务端已收到: {data}"}
            )

    except WebSocketDisconnect:
        # 只移除当前 WebSocket 实例，避免旧连接断开时误删同 thread_id 的新连接
        manager.disconnect(websocket, thread_id)
        print(f"[WebSocket] 客户端已断开: {thread_id}")

    except Exception as e:
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, thread_id)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
