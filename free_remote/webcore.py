# -*- coding: utf-8 -*-
"""HTTP 基础设施：鉴权、会话、审计、限流（不含具体路由）。"""
import secrets
import time

from .config import LOG_DIR
from .logging_util import log

SESSION_COOKIE = "fr_session"
SESSION_TTL = 24 * 3600
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


class RateLimiter:
    """登录失败限速：同一 IP 短时失败过多则临时拒绝（防暴力破解）。"""

    def __init__(self, max_fail=5, window=300, lockout=300):
        self.max_fail = max_fail
        self.window = window
        self.lockout = lockout
        self.fails = {}

    def allow(self, ip: str) -> bool:
        now = time.time()
        lst = [t for t in self.fails.get(ip, []) if now - t < self.lockout]
        self.fails[ip] = lst
        return len(lst) < self.max_fail

    def note_fail(self, ip: str):
        now = time.time()
        self.fails.setdefault(ip, []).append(now)
        self.fails[ip] = [t for t in self.fails[ip] if now - t < self.window]


def client_ip(request) -> str:
    """客户端真实 IP。
    经 frp / nginx 等反向代理时连接来自本机（127.0.0.1），此时信任
    X-Forwarded-For 的最后一个 IP（frpc 会把真实客户端 IP 追加到末尾；
    前面的部分由客户端可控，不可信）。非回环直连时直接用 socket IP。
    """
    ip = request.remote or "unknown"
    if ip in ("127.0.0.1", "::1", "unknown"):
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return ip


def audit(app, event: str, ip: str = "", detail: str = "", level: str = "INFO"):
    """记录安全事件：写入 security.log 并保留最近 50 条供 /events 查询。
    level 用于日志分级（失败/拒绝等标 WARN/ERROR）。"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{ip}] {event} {detail}".strip()
    log(level, f"[安全] {line}")
    try:
        with open(LOG_DIR / "security.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    ev = {"at": time.strftime("%H:%M:%S"), "ip": ip, "event": event, "detail": detail}
    events = app.get("events")
    if events is not None:
        events.append(ev)
        del events[:-50]
    log(level, f"[安全] {line}")
    try:
        with open(LOG_DIR / "security.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    ev = {"at": time.strftime("%H:%M:%S"), "ip": ip, "event": event, "detail": detail}
    events = app.get("events")
    if events is not None:
        events.append(ev)
        del events[:-50]

def new_session(app, token: str) -> str:
    sid = secrets.token_urlsafe(24)
    app["sessions"][sid] = {"token": token, "exp": time.time() + SESSION_TTL}
    return sid


def session_ok(app, sid: str) -> bool:
    s = app["sessions"].get(sid)
    if s is None:
        return False
    if s["exp"] < time.time():
        app["sessions"].pop(sid, None)
        return False
    return True


def ip_allowed(request) -> bool:
    args = request.app["args"]
    if not args.allow_ips:
        return True
    return client_ip(request) in args.allow_ips


def auth_ok(request) -> bool:
    """统一鉴权：IP 白名单 → 会话 Cookie（HttpOnly，防 XSS/CSRF）→ 旧版 URL token（兼容）。"""
    if not ip_allowed(request):
        return False
    args = request.app["args"]
    if args.no_auth:
        return True
    sid = request.cookies.get(SESSION_COOKIE)
    if sid and session_ok(request.app, sid):
        return True
    token = request.query.get("token", "")
    if token and secrets.compare_digest(token, args.token):
        # 固定口令若设置了有效期，运行时过期即拒绝（防止服务长期运行时口令悄悄失效/永久有效）
        exp = request.app.get("token_expire")
        if exp and time.time() > exp:
            audit(request.app, "已过期口令访问被拒绝", client_ip(request), level="WARN")
            return False
        return True
    # 一次性口令：有效期内可用（不写盘，过期/重启即废）
    once = request.app.get("once")
    if token and once and time.time() <= once["exp"] and secrets.compare_digest(token, once["tok"]):
        return True
    return False


def set_session_cookie(resp, sid: str):
    resp.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_TTL, httponly=True,
                    samesite="Lax", path="/")

