# -*- coding: utf-8 -*-
"""识别码中继模式：跨网络经中继服务器连接。"""
import asyncio
import json
import os
import random
import secrets
import sys
from urllib.parse import quote

import aiohttp
import pyautogui
from aiohttp import WSMsgType

from .capture import BlockEncoder, Streamer, _pace, cursor_frame
from .command import apply_command
from .config import BASE_DIR
from .fileshare import list_entries, resolve_fs_path, safe_file_name
from .health import HealthMonitor
from .logging_util import log, out
from .netinfo import _qr_text

# ---------------------------------------------------------------- 中继模式

def random_id() -> str:
    return str(random.randint(100000, 999999))


async def send_device_info(ws, streamer: Streamer):
    w, h = streamer.frame_size
    await ws.send_json({
        "t": "info", "w": w, "h": h,
        "preset": streamer.preset, "platform": sys.platform,
    })


def _relay_error_hint(exc: Exception) -> str:
    """把中继连接异常转成用户能看懂的原因提示。"""
    import aiohttp.client_exceptions as ce
    name = type(exc).__name__
    if isinstance(exc, ce.ClientConnectorError):
        return f"无法连接到中继（网络不通 / 中继未启动 / 端口或协议不对）：{name}"
    if isinstance(exc, ce.ClientConnectionError):
        return f"中继连接中断（服务器拒绝或网络异常）：{name}"
    if isinstance(exc, ce.ServerDisconnectedError):
        return f"中继主动断开（可能识别码被顶替或服务重启）：{name}"
    if isinstance(exc, ce.WSServerHandshakeError):
        if exc.status in (401, 403):
            return f"中继拒绝连接（识别码/口令错误，或设备未登记）：HTTP {exc.status}"
        return f"中继握手失败：HTTP {exc.status}"
    if isinstance(exc, (ce.ServerTimeoutError, asyncio.TimeoutError)):
        return f"连接中继超时（地址不可达或防火墙拦截）：{name}"
    if isinstance(exc, ce.InvalidURL):
        return f"中继地址格式错误（需以 ws:// 或 wss:// 开头）：{name}"
    return f"{name}: {exc}"


def _normalize_relay_url(addr: str) -> str:
    """把用户输入的中继地址归一化成 ws/wss 基础地址。
    容忍常见手误：漏协议、https:// 网页前缀、误带的路径后缀。"""
    addr = (addr or "").strip().rstrip("/")
    if not addr:
        return ""
    # 去掉常见误带的路径（https://域名/一串路径/ 形式）
    for scheme in ("wss://", "https://", "ws://", "http://"):
        if addr.startswith(scheme):
            rest = addr[len(scheme):]
            if "/" in rest:
                rest = rest.split("/", 1)[0]  # 只保留 主机[:端口]
            return ("wss://" if scheme in ("https://", "wss://") else "ws://") + rest
    # 无协议：IP 直连（Tailscale/局域网）按 ws；域名按 wss（公网中继一般 https 终结）
    if "/" in addr:
        addr = addr.split("/", 1)[0]
    _host = addr.split(":", 1)[0]
    if _host and all(c.isdigit() or c == "." for c in _host):
        return "ws://" + addr
    return "wss://" + addr


async def relay_client(args):
    """识别码模式：外连中继服务器，推帧上行、收指令下行。
    电脑无需公网 IP / 端口转发，手机与电脑跨网络也能互通。
    """
    # 输出跟随系统编码，仅容错不可编码字符（防 GBK 管道/终端因 emoji 崩溃）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors="replace")
        except Exception:
            pass
    pyautogui.FAILSAFE = False
    monitor = HealthMonitor(args.fps)
    streamer = Streamer(args.monitor, args.preset, args.fps, monitor)
    orig = args.relay
    base = _normalize_relay_url(args.relay)
    if base != orig.strip().rstrip("/"):
        log("INFO", f"中继地址已自动规范化：{orig.strip()} -> {base}")
    auth = f"?id={quote(args.id)}&pass={quote(args.password)}"
    url = f"{base}/pc/ws{auth}"
    file_url = f"{base}/pc/file{auth}"
    vstream_url = f"{base}/pc/vstream{auth}"
    loop = asyncio.get_running_loop()

    # 手机浏览器访问的是中继的网页入口（https/http），不是 wss
    web_base = base.replace("wss://", "https://").replace("ws://", "http://")
    print("=" * 58, flush=True)
    print(" FreeRemote 识别码模式（经中继服务器）", flush=True)
    print(f"   识别码 : {args.id}", flush=True)
    print(f"   手机访问(实时网址) : {web_base}/   （输入识别码 {args.id} + 口令）", flush=True)
    print("   免输识别码(一次性) : 用手机相机扫下面的二维码，或打开:", flush=True)
    print(f"         {web_base}/?id={args.id}&pass={args.password}", flush=True)
    print(f"   中继   : {base}", flush=True)
    print(f"   文件互传目录 : {args.share_dir}", flush=True)
    print("   停止   : Ctrl+C（断线会自动重连）", flush=True)
    print("=" * 58, flush=True)

    # 连续连接失败达到上限就退出（回 run.bat 的 pause，方便重新配置），避免无限重连
    MAX_FAILS = 5
    fails = 0
    ws_headers = None
    cookie_val = getattr(args, "cookie", "") or ""
    # 自动读取 cookie.txt（中继网关如需会话 Cookie，可手动保存至此避免每次手输）
    if not cookie_val:
        cf = BASE_DIR / "cookie.txt"
        if cf.exists():
            try:
                cookie_val = cf.read_text(encoding="utf-8").strip()
            except OSError:
                cookie_val = ""
    # 模拟真实浏览器的请求头：部分网关只放行带浏览器 UA/Origin 的 WS 升级
    ws_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }
    try:
        _u = url.split("://", 1)[1].split("/", 1)[0]
        ws_headers["Origin"] = f"https://{_u}"
    except Exception:
        pass
    if cookie_val:
        ws_headers["Cookie"] = cookie_val
        log("INFO", "已携带网关会话 Cookie 连接（--cookie / cookie.txt）")
    else:
        log("INFO", "未携带 Cookie 连接（如中继网关要求会话 Cookie，可写入 cookie.txt）")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                ws_main = await session.ws_connect(url, heartbeat=30, headers=ws_headers)
                ws_file = await session.ws_connect(file_url, heartbeat=30, headers=ws_headers)
                ws_v = await session.ws_connect(vstream_url, heartbeat=30, headers=ws_headers)
                fails = 0  # 连接成功：重置失败计数（断线重连不累计）
                log("INFO", f"已连接中继：{base}")
                monitor.note_reconnect()
                await send_device_info(ws_main, streamer)
                await _issue_login_qr(ws_main, base, args)
                tasks = {
                    asyncio.create_task(_command_loop(ws_main, streamer, loop)),
                    asyncio.create_task(_capture_loop(ws_main, streamer, monitor, loop)),
                    asyncio.create_task(_telemetry_loop(ws_main, streamer, monitor, args)),
                    asyncio.create_task(_file_loop(ws_file, args)),
                    asyncio.create_task(_vstream_loop(ws_v, streamer, monitor, loop)),
                }
                try:
                    done, _pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_EXCEPTION)
                finally:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for t in done:
                    exc = t.exception()
                    if exc and not isinstance(exc, asyncio.CancelledError):
                        raise exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            fails += 1
            hint = _relay_error_hint(exc)
            if fails >= MAX_FAILS:
                log("ERROR", f"中继连接连续失败 {MAX_FAILS} 次：{hint}")
                print("=" * 58, flush=True)
                print(" 连接失败，已停止重试。请检查下面几项后重新运行 run.bat：", flush=True)
                print(f"   1. 中继地址  : {base}", flush=True)
                print(f"   2. 识别码    : {args.id}", flush=True)
                print("   3. 中继是否已启动、地址/端口/协议(ws|wss)是否正确", flush=True)
                print("   4. 识别码/口令与中继端 --devices 登记的必须一致", flush=True)
                print("   重新配置   : 双击 run.bat，按提示重新输入（或改 relay-config.txt 后重跑）", flush=True)
                print("=" * 58, flush=True)
                return 1
            log("WARN", f"中继连接失败({fails}/{MAX_FAILS})：{hint}，5 秒后重连…")
            await asyncio.sleep(5)


async def _issue_login_qr(ws_main, base, args):
    """生成扫码登录兑换券：发给中继登记（120s 一次性），并在终端打印二维码。
    手机用相机扫终端二维码 → 打开 中继/login?code=xxx → 自动登录，免输识别码。
    """
    code = secrets.token_urlsafe(9)
    try:
        await ws_main.send_str(json.dumps({"t": "loginvoucher", "code": code}))
    except Exception:
        return
    url = f"{base}/login?code={code}"
    out("")
    out("=" * 58)
    out(" 扫码登录（120 秒内有效，用手机相机扫下面的二维码）")
    out(f"    或手动打开: {url}")
    try:
        import qrcode  # 可选依赖，缺省时仅打印链接
        qr = qrcode.QRCode(border=1, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        out(_qr_text(qr))
    except Exception:
        pass
    out("=" * 58)
    out("")


async def _vstream_loop(ws_v, streamer, monitor, loop):
    """中继分块推流：把变化块与光标经 pc/vstream 推给中继转发。"""
    enc = BlockEncoder(streamer)
    hb_task = loop.create_task(_relay_heartbeat(ws_v))
    try:
        while True:
            t0 = loop.time()
            if monitor.check_gil_pause(streamer):
                await _pace(loop, 1.0, t0)
                continue
            rgb, w, h = await loop.run_in_executor(None, streamer.capture_rgb)
            if rgb:
                monitor.maybe_heal(streamer)
                cx, cy = cursor_frame(streamer)
                msgs = await loop.run_in_executor(None, enc.diff, rgb, w, h, cx, cy)
                for m in msgs:
                    await ws_v.send_bytes(m)
            await _pace(loop, streamer.fps, t0)
    finally:
        hb_task.cancel()


async def _relay_heartbeat(ws):
    """中继分块通道保活：静止时也保持消息流（客户端冻结检测基准）。"""
    try:
        while True:
            await asyncio.sleep(5)
            await ws.send_str(json.dumps({"t": "hb"}))
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _command_loop(ws, streamer, loop):
    """中继下行：控制指令。"""
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                cmd = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            # 统一入口：所有指令都交给 apply_command；画质/分辨率变化额外回报设备信息
            await loop.run_in_executor(None, apply_command, streamer, cmd)
            if cmd.get("t") in ("setpreset", "setres"):
                await send_device_info(ws, streamer)
        elif msg.type == WSMsgType.ERROR:
            break



async def _capture_loop(ws, streamer, monitor, loop):
    """中继上行：屏幕帧推流。"""
    while True:
        t0 = loop.time()
        if monitor.check_gil_pause(streamer):
            await _pace(loop, 1.0, t0)
            continue
        data = await loop.run_in_executor(None, streamer.capture)
        if data == streamer._fallback:
            monitor.note_fail(Exception("capture returned fallback"))  # 占位图=采集失败
        monitor.maybe_heal(streamer)  # 自愈
        await ws.send_bytes(data)
        await _pace(loop, streamer.fps, t0)


async def _telemetry_loop(ws, streamer, monitor, args):
    """中继上行：定期上报自检数据。"""
    while True:
        try:
            await ws.send_json({"t": "telemetry", "data": monitor.report(
                streamer, mode="relay",
                no_auth=args.no_auth, token_len=len(args.password),
            )})
        except Exception:
            return
        await asyncio.sleep(5)


async def _file_loop(ws, args):
    """中继文件通道：接收中继转发的上传/下载/列表请求并处理。
    协议：text 为请求（带 tid），binary 为上传数据块（前 4 字节 tid）。
    """
    os.makedirs(args.share_dir, exist_ok=True)
    uploads = {}  # tid -> {tmp, fh}
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    req = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                kind = req.get("kind")
                tid = req.get("id")
                try:
                    if kind == "upload_start":
                        name = safe_file_name(req.get("name", "upload.bin"))
                        dirpath = resolve_fs_path(args.share_dir, req.get("path", ""))
                        os.makedirs(dirpath, exist_ok=True)
                        tmp = os.path.join(dirpath, f".{name}.{tid}.part")
                        uploads[tid] = {"tmp": tmp, "fh": open(tmp, "wb")}
                    elif kind == "upload_end":
                        u = uploads.pop(tid, None)
                        if u:
                            u["fh"].close()
                            name = safe_file_name(req.get("name", "upload.bin"))
                            dirpath = resolve_fs_path(args.share_dir, req.get("path", ""))
                            os.replace(u["tmp"], os.path.join(dirpath, name))
                            await ws.send_json({"t": "resp", "id": tid, "kind": "done"})
                        else:
                            await ws.send_json({"t": "resp", "id": tid, "kind": "err", "msg": "未知传输"})
                    elif kind == "download":
                        path = resolve_fs_path(args.share_dir, req.get("path", ""))
                        if not os.path.isfile(path):
                            raise FileNotFoundError(path)
                        size = os.path.getsize(path)
                        name = os.path.basename(path)
                        await ws.send_json({"t": "resp", "id": tid, "kind": "dstart",
                                            "name": name, "size": size})
                        with open(path, "rb") as f:
                            while True:
                                chunk = f.read(256 * 1024)
                                if not chunk:
                                    break
                                await ws.send_bytes(tid.to_bytes(4, "big") + chunk)
                        await ws.send_json({"t": "resp", "id": tid, "kind": "dend"})
                    elif kind == "list":
                        path = resolve_fs_path(args.share_dir, req.get("path", ""))
                        await ws.send_json({"t": "resp", "id": tid, "kind": "list",
                                            "path": path, "parent": os.path.dirname(path),
                                            "entries": list_entries(path)})
                    elif kind == "cancel":
                        u = uploads.pop(tid, None)
                        if u:
                            u["fh"].close()
                            try:
                                os.remove(u["tmp"])
                            except OSError:
                                pass
                except Exception as e:
                    try:
                        await ws.send_json({"t": "resp", "id": tid, "kind": "err", "msg": str(e)})
                    except Exception:
                        pass
            elif msg.type == WSMsgType.BINARY:
                data = msg.data
                if len(data) < 5:
                    continue
                tid = int.from_bytes(data[:4], "big")
                u = uploads.get(tid)
                if u:
                    u["fh"].write(data[4:])
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        for u in uploads.values():
            try:
                u["fh"].close()
            except Exception:
                pass
            try:
                os.remove(u["tmp"])
            except OSError:
                pass

