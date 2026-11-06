from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import SESSION_COOKIE, auth_manager
from .config_manager import config_manager
from .models import AppSettings
from .scheduler import broadcast_scheduler
from .wechat_sender import wechat_sender

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="三国群雄传平台司令台", version="1.2.0")

PUBLIC_PATHS = {
    "/login",
    "/api/health",
    "/api/auth/config",
    "/api/auth/local_enter",
    "/api/auth/me",
    "/api/auth/logout",
    "/api/wechat/status",
    "/api/wechat/wake",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path.startswith("/static/"):
            return await call_next(request)

        if path in PUBLIC_PATHS or path.startswith("/api/auth/"):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE)
        session = auth_manager.get_session(token)
        if session:
            request.state.user = session
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "未登录或会话已过期", "code": "UNAUTHORIZED"},
                status_code=401,
            )
        return RedirectResponse(url="/login", status_code=302)


app.add_middleware(AuthMiddleware)


@app.on_event("startup")
def on_startup() -> None:
    broadcast_scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    broadcast_scheduler.shutdown()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# ---------- Auth APIs ----------


@app.get("/api/health")
def health():
    return {"ok": True, "name": "三国群雄传平台司令台"}


@app.get("/api/auth/config")
def auth_config():
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    session = auth_manager.get_session(request.cookies.get(SESSION_COOKIE))
    if not session:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "nickname": session.nickname,
        "openid": session.openid,
        "avatar": session.avatar,
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth_manager.destroy_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    _clear_session_cookie(response)
    return response


@app.post("/api/auth/local_enter")
def local_enter():
    """本机一键进入司令台。"""
    session = auth_manager.create_session("本机管理员", "local_enter")
    resp = JSONResponse({"ok": True, "nickname": session.nickname})
    _set_session_cookie(resp, session.token)
    return resp


# ---------- Business APIs ----------


@app.get("/api/settings")
def get_settings():
    return config_manager.get()


@app.put("/api/settings")
def put_settings(settings: AppSettings):
    updated = config_manager.update(settings)
    return {"ok": True, "settings": updated}


@app.patch("/api/settings")
def patch_settings(partial: dict):
    try:
        updated = config_manager.patch(partial)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "settings": updated}


@app.get("/api/jobs")
def get_jobs():
    return {"jobs": broadcast_scheduler.list_jobs()}


@app.get("/api/logs")
def get_logs(limit: int = 50):
    return {"logs": wechat_sender.get_logs(limit)}


@app.get("/api/wechat/status")
def wechat_status():
    """登录页可用：检测本机 PC 微信进程/窗口（不强制唤醒）。"""
    return wechat_sender.probe_status(try_wake=False)


@app.post("/api/wechat/wake")
def wechat_wake():
    """登录页可用：尝试用快捷键唤醒微信并回报状态。"""
    return wechat_sender.probe_status(try_wake=True)


@app.post("/api/wechat/test")
def test_wechat():
    return wechat_sender.test_connection()


@app.post("/api/broadcast/preview")
def preview_broadcast(body: dict):
    feature = body.get("feature", "手动试发")
    message = body.get("message", "").strip()
    members = body.get("members") or []
    if not message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    log = wechat_sender.send(
        feature, message, at_members=members, immediate=True
    )
    return log


@app.post("/api/scheduler/reload")
def reload_scheduler():
    broadcast_scheduler.reload()
    return {"ok": True, "jobs": broadcast_scheduler.list_jobs()}


# ---------- Pages ----------


@app.get("/login")
def login_page(request: Request):
    session = auth_manager.get_session(request.cookies.get(SESSION_COOKIE))
    if session:
        return RedirectResponse(url="/")
    return FileResponse(FRONTEND / "login.html")


@app.get("/")
def index(request: Request):
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")
