# -*- coding: utf-8 -*-
"""HTTP/WS 路由与应用装配。"""
import asyncio
import json
import secrets
import sys
import time

from aiohttp import web, WSMsgType

from .capture import Broadcaster, BlockEncoder, FrameSource, HealthMonitor, Streamer, cursor_frame
from .command import apply_command, force_release_all_mods
from .config import BASE_DIR, BOUNDARY, QUALITY_PRESETS
from .fileshare import download_handler, files_handler, upload_handler
from .webcore import (
    NO_CACHE, RateLimiter,
    audit, auth_ok, client_ip, ip_allowed, new_session, set_session_cookie,
)

async def login_handler(request):
    """登录：校验口令后签发 HttpOnly 会话 Cookie，口令不再留在 URL。"""
    args = request.app["args"]
    ip = client_ip(request)
    if not ip_allowed(request):
        audit(request.app, "登录被 IP 白名单拒绝", ip, level="ERROR")
        return web.Response(status=403, text="IP 不在白名单")
    if args.no_auth:
        return web.Response(status=302, headers={"Location": "/"})
    rl = request.app["ratelimit"]
    if not rl.allow(ip):
        audit(request.app, "登录被限速拒绝(暴力破解防护)", ip, level="ERROR")
        return web.Response(status=429, text="尝试过于频繁，请 5 分钟后再试")
    token = request.query.get("token", "")
    if not token and request.method == "POST":
        data = await request.post()
        token = (data.get("token") or "").strip()
    ok = bool(token) and secrets.compare_digest(token, args.token)
    if ok:
        exp = request.app.get("token_expire")
        if exp and time.time() > exp:
            ok = False  # 固定口令已过期，登录拒绝（一次性口令不走此分支）
    if not ok:
        once = request.app.get("once")
        ok = bool(token) and once and time.time() <= once["exp"] and secrets.compare_digest(token, once["tok"])
    if not ok:
        rl.note_fail(ip)
        audit(request.app, "登录失败(口令错误)", ip, level="WARN")
        return web.Response(status=403, text="口令错误")
    sid = new_session(request.app, token)
    audit(request.app, "登录成功", ip)
    resp = web.Response(status=302, headers={"Location": "/"})
    set_session_cookie(resp, sid)
    return resp


async def mode_handler(request):
    """返回运行模式，供手机端登录界面判断。"""
    return web.json_response({"mode": "lan"})


async def events_handler(request):
    """最近安全事件（登录/文件/连接），供自检页与审计。"""
    if not auth_ok(request):
        return web.Response(status=403)
    return web.json_response({"ok": True, "events": request.app.get("events", [])})


async def index_handler(request):
    # 页面总是可访问（登录由客户端 JS 处理）；数据接口统一走 auth_ok
    return web.FileResponse(BASE_DIR / "web" / "index.html", headers=NO_CACHE)


async def doctor_handler(request):
    """手机端自检仪表盘页面（数据来自 /status）。"""
    return web.FileResponse(BASE_DIR / "web" / "doctor.html", headers=NO_CACHE)


async def manifest_handler(request):
    """PWA manifest（手机"添加到主屏幕"用）。"""
    return web.FileResponse(BASE_DIR / "web" / "manifest.json",
                            headers={"Content-Type": "application/manifest+json"})


async def icon_handler(request):
    """PWA 图标。"""
    name = request.match_info.get("name", "")
    path = (BASE_DIR / "web" / "icons" / name).resolve()
    if path.parent != (BASE_DIR / "web" / "icons").resolve() or not path.is_file():
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Content-Type": "image/png"})


async def status_handler(request):
    """运行时自检报告（JSON）：供手机端自检页与外部监控使用。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    monitor = request.app["monitor"]
    streamer = request.app["streamer"]
    return web.json_response(monitor.report(
        streamer, mode="lan",
        no_auth=args.no_auth, token_len=len(args.token),
    ))


async def info_handler(request):
    if not auth_ok(request):
        return web.Response(status=403)
    s = request.app["streamer"]
    w, h = s.frame_size
    return web.json_response(
        {"w": w, "h": h, "platform": sys.platform, "preset": s.preset,
         "scale": s.scale, "user_scale": s.user_scale}
    )


async def stream_handler(request):
    """MJPEG 推流：multipart/x-mixed-replace，手机 <img> 直接播放。"""
    if not auth_ok(request):
        return web.Response(status=403)
    s = request.app["streamer"]
    q = request.query.get("q", s.preset)
    if q in QUALITY_PRESETS:
        s.set_preset(q)

    broadcaster = request.app["broadcaster"]
    queue = broadcaster.subscribe()
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
            "Cache-Control": "no-cache, no-store",
            "Connection": "close",
        },
    )
    await resp.prepare(request)
    try:
        while True:
            data = await queue.get()
            await resp.write(
                b"--" + BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                + data + b"\r\n"
            )
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        broadcaster.unsubscribe(queue)
    return resp


async def vstream_handler(request):
    """分块增量推流（WebSocket）：只发变化块 + 光标，显著降低延迟/带宽。"""
    if not auth_ok(request):
        return web.Response(status=403)
    streamer = request.app["streamer"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    enc = BlockEncoder(streamer)
    w, h = streamer.frame_size
    cols, rows = enc.grid(w, h)
    await ws.send_json({"t": "init", "w": w, "h": h, "cols": cols, "rows": rows,
                        "block": enc.BLOCK})
    loop = asyncio.get_running_loop()
    q = request.app["frames"].subscribe()
    try:
        hb_task = loop.create_task(_vstream_heartbeat(ws, streamer))
        try:
            while True:
                rgb, w, h = await q.get()
                cx, cy = cursor_frame(streamer)
                msgs = await loop.run_in_executor(None, enc.diff, rgb, w, h, cx, cy)
                for m in msgs:
                    await ws.send_bytes(m)
        finally:
            hb_task.cancel()
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    finally:
        request.app["frames"].unsubscribe(q)
    return ws


async def _vstream_heartbeat(ws, streamer):
    """分块通道保活：画面静止时每 5 秒发一个心跳，兼作客户端冻结检测基准。

    桌面不动时 diff 产出 0 字节，客户端无法区分"静止"与"死链"。心跳是
    服务端"我还活着"的证据，客户端 12 秒收不到任何消息（含心跳）即判定
    推流卡死并自动恢复。
    """
    try:
        while True:
            await asyncio.sleep(5)
            await ws.send_str(json.dumps({"t": "hb"}))
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def ws_handler(request):
    if not auth_ok(request):
        return web.Response(status=403)
    streamer = request.app["streamer"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    audit(request.app, "控制连接建立", client_ip(request))
    loop = asyncio.get_running_loop()
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    cmd = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                await loop.run_in_executor(None, apply_command, streamer, cmd)
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        # 断线（手机锁屏/切后台/网络切换）时强制释放修饰键，防止电脑端卡键
        await loop.run_in_executor(None, force_release_all_mods, "控制连接断开")
    audit(request.app, "控制连接断开", client_ip(request))
    return ws


async def on_shutdown(app):
    await app["broadcaster"].stop()


def make_app(args) -> web.Application:
    monitor = HealthMonitor(args.fps)
    streamer = Streamer(args.monitor, args.preset, args.fps, monitor)
    broadcaster = Broadcaster(streamer)
    frames = FrameSource(streamer)
    # 观看源自注册：观看者计数的唯一事实来源在 Streamer（供健康监控查询）
    streamer.register_view_source(frames)
    streamer.register_view_source(broadcaster)
    app = web.Application()
    app["args"] = args
    app["streamer"] = streamer
    app["monitor"] = monitor
    app["broadcaster"] = broadcaster
    app["frames"] = frames
    app["sessions"] = {}  # sid -> {token, exp}（HttpOnly Cookie 会话）
    app["ratelimit"] = RateLimiter()  # 登录失败限速（防暴力破解）
    app["events"] = []  # 最近安全事件（登录/文件/连接，供 /events 与审计）
    app.router.add_get("/", index_handler)
    app.router.add_get("/login", login_handler)
    app.router.add_post("/login", login_handler)
    app.router.add_get("/mode", mode_handler)
    app.router.add_get("/events", events_handler)
    app.router.add_get("/info", info_handler)
    app.router.add_get("/stream", stream_handler)
    app.router.add_get("/vstream", vstream_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/status", status_handler)
    app.router.add_get("/doctor", doctor_handler)
    app.router.add_get("/files", files_handler)
    app.router.add_post("/upload", upload_handler)
    app.router.add_get("/download", download_handler)
    app.router.add_get("/manifest.json", manifest_handler)
    app.router.add_get("/icons/{name}", icon_handler)
    # MJPEG 广播器为懒启动：有订阅者才采集，避免与分块通道重复采集
    app.on_shutdown.append(on_shutdown)
    return app

