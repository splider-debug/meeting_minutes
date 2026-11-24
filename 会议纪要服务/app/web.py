# -*- coding: utf-8 -*-
"""M4 Web 层：任务队列 + SSE 进度流 + 单页。

与 M2 冒烟版的区别：
  · 上传立即返回 task_id，任务在后台线程跑，不会把 20 分钟的请求挂在反代上；
  · 进度通过 SSE 推送（阶段/步骤/指标/完成/失败）；
  · 页面显示阶段条、步骤流、下载链接与 1 小时倒计时。

并发与存储：单进程内存队列 + 每任务目录（成品 1 小时 TTL）。
"历史任务列表"按需求明确不做，所以没有持久化任务表。
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import config
from .errors import AppError
from .page import INDEX_HTML
from .pipeline import STAGES, run_task

SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(16)
PASSWORDS = [p.strip() for p in os.environ.get("AUTH_PASSWORDS", "").split(",") if p.strip()]
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", "2592000"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(config.SERVICE_DIR, "data"))
ARTIFACT_TTL_SEC = int(os.environ.get("ARTIFACT_TTL_SEC", "3600"))
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "2"))
HEARTBEAT_SEC = 15
POLL_SEC = 0.4

app = FastAPI(title="会议资料整理服务", version="0.4.0-m4")

# task_id -> state dict
TASKS: dict[str, dict] = {}
_sem: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_CONCURRENCY)
    return _sem


# ---------------------------------------------------------------- 认证
def _sign(exp: int) -> str:
    mac = hmac.new(SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return "%d.%s" % (exp, mac)


def _check(cookie: str | None) -> bool:
    if not cookie or "." not in cookie:
        return False
    exp_s, mac = cookie.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    return exp >= time.time() and hmac.compare_digest(_sign(exp), "%s.%s" % (exp_s, mac))


def _authed(req: Request) -> bool:
    return _check(req.cookies.get("session"))


@app.get("/")
def index():
    html = (INDEX_HTML
            .replace("__TTL__", str(ARTIFACT_TTL_SEC // 60))
            .replace("__MAXMB__", str(config.MAX_UPLOAD_MB))
            .replace("__STAGES__", json.dumps([[c, n] for c, n in STAGES], ensure_ascii=False)))
    return StreamingResponse(iter([html.encode("utf-8")]), media_type="text/html; charset=utf-8")


@app.get("/api/me")
def me(req: Request):
    return {"authed": _authed(req)}


@app.post("/auth/login")
def login(password: str = Form("")):
    if not PASSWORDS:
        return JSONResponse({"code": "AUTH_NOT_CONFIGURED",
                             "message": "服务端未配置 AUTH_PASSWORDS"}, status_code=500)
    if not any(hmac.compare_digest(password, p) for p in PASSWORDS):
        return JSONResponse({"code": "AUTH_REQUIRED", "message": "口令不对"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("session", _sign(int(time.time()) + SESSION_MAX_AGE),
                    max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.post("/auth/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("session")
    return resp


# ---------------------------------------------------------------- 健康检查
@app.get("/healthz")
def healthz():
    from .tools_bridge import TOOLS_VERSION
    return {"ok": True, "version": app.version, "tools": str(config.TOOLS_DIR),
            "tools_version": TOOLS_VERSION}


@app.get("/readyz")
def readyz():
    checks = {"tools_ok": (config.TOOLS_DIR / "md2docx.py").is_file(),
              "data_writable": False, "auth_configured": bool(PASSWORDS)}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        p = os.path.join(DATA_DIR, ".probe")
        with open(p, "w") as f:
            f.write("ok")
        os.remove(p)
        checks["data_writable"] = True
    except OSError:
        pass
    ok = all(checks.values())
    return JSONResponse({"ok": ok, **checks}, status_code=200 if ok else 503)


# ---------------------------------------------------------------- 任务
def _emit_factory(task: dict, loop: asyncio.AbstractEventLoop):
    """pipeline 的 emit 在工作线程里跑，事件统一投递回事件循环。"""
    def emit(kind, **kw):
        if kind == "**":
            ev = {"event": kw.get("state") and "stage" or "stage", **kw}
            ev.pop("event", None)
            payload = {"event": "stage", "stage": kw.get("stage"), "name": kw.get("name"),
                       "state": kw.get("state"), "elapsed": kw.get("elapsed"),
                       "detail": kw.get("detail", "")}
        else:
            payload = {"event": "step", "label": kw.get("label", ""), "detail": kw.get("detail", "")}
        loop.call_soon_threadsafe(task["events"].append, payload)
    return emit


async def _run(task_id: str) -> None:
    task = TASKS.get(task_id)
    if not task:
        return
    loop = asyncio.get_running_loop()
    emit = _emit_factory(task, loop)
    src = task["src"]
    out_dir = task["dir"]
    try:
        async with _semaphore():
            task["state"] = "running"
            result = await asyncio.to_thread(
                run_task, src, out_dir, task.get("title") or None, emit)
        files = []
        for key, label in (("md", "逐字转写.md"), ("docx", "交付稿.docx"), ("zip", "全部打包.zip")):
            p = result.get(key)
            if p and os.path.isfile(p):
                files.append({"name": label, "url": "/files/%s/%s" % (task_id, os.path.basename(p)),
                              "bytes": os.path.getsize(p)})
        plan = result.get("plan") or {}
        task["events"].append({"event": "metric",
                               "chapters": len(plan.get("chapters") or []),
                               "sections": sum(len(c["sections"]) for c in (plan.get("chapters") or [])),
                               "tables": len(plan.get("tables") or [])})
        task["events"].append({
            "event": "done", "task_id": task_id, "files": files,
            "title": result["title"], "elapsed": result["elapsed"],
            "guard": {"ok": bool((result.get("guard") or {}).get("ok"))},
            "verify": (result.get("verify") or {}).get("conclusion", ""),
            "expires_at": int(time.time()) + ARTIFACT_TTL_SEC,
        })
        task["state"] = "done"
        task["expires_at"] = int(time.time()) + ARTIFACT_TTL_SEC
    except AppError as e:
        task["events"].append({"event": "failed", "code": e.code,
                               "message": e.message, "retryable": e.retryable})
        task["state"] = "failed"
    except Exception as e:                       # 兜底：不把堆栈给前端
        task["events"].append({"event": "failed", "code": "INTERNAL",
                               "message": "%s: %s" % (type(e).__name__, e)})
        task["state"] = "failed"


@app.post("/api/tasks")
async def create_task(request: Request, file: UploadFile = File(...), title: str = Form("")):
    if not _authed(request):
        return JSONResponse({"code": "AUTH_REQUIRED", "message": "未登录"}, status_code=401)

    # 单 IP 同时只允许一个进行中的任务
    client = request.client.host if request.client else "?"
    for t in TASKS.values():
        if t.get("client") == client and t["state"] in ("queued", "running"):
            return JSONResponse({"code": "RATE_LIMITED",
                                 "message": "当前有一个任务在进行中，请等它结束"}, status_code=429)

    task_id = uuid.uuid4().hex[:12]
    work = os.path.join(DATA_DIR, task_id)
    os.makedirs(work, exist_ok=True)
    name = os.path.basename(file.filename or "input")
    src = os.path.join(work, name)

    written = 0
    with open(src, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > config.MAX_UPLOAD_MB * 1024 * 1024:
                f.close()
                shutil.rmtree(work, ignore_errors=True)
                return JSONResponse({"code": "FILE_TOO_LARGE",
                                     "message": "超过 %d MB" % config.MAX_UPLOAD_MB},
                                    status_code=413)
            f.write(chunk)

    TASKS[task_id] = {"id": task_id, "state": "queued", "src": src, "dir": work,
                      "title": title, "client": client, "events": [],
                      "created": time.time(), "expires_at": None}
    asyncio.create_task(_run(task_id))
    return {"task_id": task_id}


@app.get("/api/tasks/{task_id}")
def task_snapshot(request: Request, task_id: str):
    if not _authed(request):
        return JSONResponse({"code": "AUTH_REQUIRED", "message": "未登录"}, status_code=401)
    t = TASKS.get(task_id)
    if not t:
        return JSONResponse({"code": "NOT_FOUND", "message": "任务不存在或已过期"}, status_code=404)
    return {"task_id": task_id, "state": t["state"], "events": t["events"][-80:],
            "expires_at": t["expires_at"]}


@app.get("/api/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str):
    if not _authed(request):
        return JSONResponse({"code": "AUTH_REQUIRED", "message": "未登录"}, status_code=401)
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"code": "NOT_FOUND", "message": "任务不存在或已过期"}, status_code=404)

    try:
        cursor = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        cursor = 0

    async def stream():
        nonlocal cursor
        last_beat = time.time()
        seq = 0
        while True:
            events = task["events"]
            while cursor < len(events):
                ev = events[cursor]
                cursor += 1
                seq += 1
                yield "id: %d\nevent: %s\ndata: %s\n\n" % (
                    seq, ev.get("event", "message"),
                    json.dumps({k: v for k, v in ev.items() if k != "event"}, ensure_ascii=False))
                if ev.get("event") in ("done", "failed"):
                    return
            if await request.is_disconnected():
                return
            if time.time() - last_beat > HEARTBEAT_SEC:
                last_beat = time.time()
                yield ": ping\n\n"
            await asyncio.sleep(POLL_SEC)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/tasks/{task_id}/result")
def task_result(request: Request, task_id: str):
    if not _authed(request):
        return JSONResponse({"code": "AUTH_REQUIRED", "message": "未登录"}, status_code=401)
    t = TASKS.get(task_id)
    if not t or not t.get("expires_at"):
        return JSONResponse({"code": "NOT_READY", "message": "任务未完成"}, status_code=409)
    p = os.path.join(t["dir"], "result.zip")
    if not os.path.isfile(p) or time.time() > t["expires_at"]:
        return JSONResponse({"code": "GONE", "message": "成品保留 1 小时，已过期"}, status_code=410)
    return FileResponse(p, filename="result.zip")


@app.get("/files/{task_id}/{name}")
def download(request: Request, task_id: str, name: str):
    if not _authed(request):
        return JSONResponse({"code": "AUTH_REQUIRED", "message": "未登录"}, status_code=401)
    if not task_id.isalnum() or "/" in name or "\\" in name or name.startswith("."):
        return JSONResponse({"code": "BAD_PATH", "message": "非法路径"}, status_code=400)
    p = os.path.join(DATA_DIR, task_id, name)
    if not os.path.isfile(p):
        return JSONResponse({"code": "GONE", "message": "成品保留 1 小时，已过期或不存在"}, status_code=410)
    if time.time() - os.path.getmtime(p) > ARTIFACT_TTL_SEC:
        return JSONResponse({"code": "GONE", "message": "成品保留 1 小时，已过期"}, status_code=410)
    return FileResponse(p, filename=name)


# ---------------------------------------------------------------- 清理
@app.on_event("startup")
async def _startup():
    async def janitor():
        while True:
            now = time.time()
            for tid, t in list(TASKS.items()):
                if t["state"] in ("done", "failed") and t.get("expires_at"):
                    if now > t["expires_at"] + 600:
                        shutil.rmtree(t["dir"], ignore_errors=True)
                        TASKS.pop(tid, None)
                elif t["state"] in ("done", "failed") and not t.get("expires_at"):
                    if now - t["created"] > ARTIFACT_TTL_SEC + 600:
                        shutil.rmtree(t["dir"], ignore_errors=True)
                        TASKS.pop(tid, None)
            await asyncio.sleep(60)

    asyncio.create_task(janitor())
