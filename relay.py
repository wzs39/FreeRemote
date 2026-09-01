#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeRemote 中继服务器（识别码配对，跨网络远程控制）

让手机与电脑在不同网络下通过「识别码 + 口令」精准配对：
  - 电脑主动外连中继，持续推送屏幕帧并接收控制指令（无需公网 IP / 端口转发）
  - 手机浏览器打开中继网址，输入识别码 + 口令即可监看和控制

部署（任选其一）：
  1. 任意 VPS / 云主机：  python3 relay.py --port 9090
  2. Render / Railway / Fly.io 等平台：把本项目部署为 web 服务，启动命令同上
  推荐用 Caddy / Cloudflare 加上 HTTPS（这样手机端自动走 wss），口令请用强密码。

电脑端（识别码模式）：
  python server.py --relay wss://你的域名 --id ABC123 --password mypass

手机端：
  打开 https://你的域名/?id=ABC123&pass=mypass
"""

import argparse
import asyncio
import hashlib
import io
import json
import os
import secrets
import socket
import time
from pathlib import Path

from aiohttp import web, WSMsgType
from PIL import Image, ImageDraw

BOUNDARY = b"frame"
BASE_DIR = Path(__file__).resolve().parent

SESSION_COOKIE = "fr_session"
SESSION_TTL = 24 * 3600

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "relay.log"


def log(level: str, msg: str):
    """统一日志：控制台打印 [时间] [级别] 消息，同时强制 UTF-8 落盘 relay.log。
    level 取值：INFO / WARN / ERROR（错误行统一带 [ERROR]，可直接 grep）。"""
    line = f"[{time.strftime('%H:%M:%S')}] [{level}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def out(msg: str = ""):
    """横幅输出：控制台打印 + UTF-8 伴写日志文件（无级别前缀，保持横幅格式）。"""
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def client_ip(request, trusted: bool = False) -> str:
    """客户端真实 IP。经反向代理（Caddy/Nginx/frp/Render/Fly.io）时，来源地址是代理而非用户，
    需要信任 X-Forwarded-For 的最后一个 IP（代理会把真实 IP 追加到末尾；前面的由客户端可控，不可信）。
    回环来源自动信任 XFF；`trusted=True`（--xf-trusted，用于部署在 TLS 终结平台后方）则无条件信任。"""
    ip = request.remote or "?"
    if trusted or ip in ("127.0.0.1", "::1", "?"):
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return ip


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


def make_placeholder(text: str) -> bytes:
    """设备离线时给手机端显示一张占位图。"""
    img = Image.new("RGB", (640, 360), (18, 20, 26))
    draw = ImageDraw.Draw(img)
    draw.text((24, 150), text, fill=(150, 160, 180))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=60)
    return buf.getvalue()


class DeviceConn:
    """一台已注册电脑的连接（含心跳/会话监控信息）。"""

    def __init__(self, ws):
        self.ws = ws
        self.file_ws = None  # 文件传输专用通道
        self.vstream_ws = None  # 分块推流专用通道
        self.vstream_phones = set()  # 每个手机 /vstream 订阅一个 asyncio.Queue
        self.last_full = None  # 最近一帧全帧消息（新手机连接时先发缓存帧）
        self.info = {"w": 0, "h": 0, "preset": "", "platform": ""}
        self.telemetry = None  # 电脑端周期性上报的自检数据（fps/错误/补救记录等）
        self.phones = set()  # 每个手机 /stream 订阅一个 asyncio.Queue
        # —— 心跳 / 会话监控 ——
        self.started = time.time()  # 墙上时钟，用于显示
        self.started_mono = time.monotonic()  # 单调时钟，用于计算时长/心跳
        self.last_activity = time.monotonic()  # 最近一次有消息的时间（单调）

    @property
    def alive(self):
        return bool(self.last_activity)  # 是否收过消息

    @property
    def last_seen_sec(self) -> float:
        return time.monotonic() - self.last_activity if self.last_activity else -1.0

    @property
    def uptime(self) -> int:
        return int(time.monotonic() - self.started_mono)


class Relay:
    def __init__(self, devices: dict, auto_register: bool,
                 conn_policy: str = "replace", hb_timeout: int = 90, hb_interval: int = 10,
                 xf_trusted: bool = False, wol: dict = None, wol_bcast: str = "255.255.255.255"):
        self.hashes = {
            k: hashlib.sha256(v.encode()).hexdigest() for k, v in devices.items()
        }
        self.wol = wol or {}            # 识别码 -> MAC 地址（Wake-on-LAN 远程唤醒）
        self.wol_bcast = wol_bcast      # WoL 广播地址（默认全子网广播）
        self.vouchers = {}              # 登录兑换券 code -> {device_id, exp}（扫码登录）
        self.auto_register = auto_register
        self.xf_trusted = xf_trusted
        self.conn_policy = conn_policy  # replace | reject：同识别码多端连接策略
        self.hb_timeout = hb_timeout  # 心跳超时（秒），超过视为断线并强制重连
        self.hb_interval = hb_interval  # 心跳扫描间隔
        self.conns: dict[str, DeviceConn] = {}
        self.placeholder = make_placeholder("等待设备上线……")
        self.start = time.monotonic()
        self.pending = {}  # 文件传输 tid -> asyncio.Queue（中转手机 HTTP 请求与电脑响应的应答）
        self._tid = 0
        self.sessions = {}  # sid -> {device_id, password, exp}（HttpOnly Cookie 会话）
        self.ratelimit = RateLimiter()  # 登录失败限速（防暴力破解）
        self.events = []  # 最近安全事件（登录/设备上下线，供 /events 审计）
        # —— 会话统计 ——
        self.stats = {"device_sessions": 0, "phone_control": 0,
                      "phone_watch": 0, "devices_seen": set()}
        self.hb_task = None

    def next_tid(self) -> int:
        self._tid += 1
        return self._tid

    def auth_ok(self, device_id: str, password: str) -> bool:
        if not device_id or not password:
            return False
        h = hashlib.sha256(password.encode()).hexdigest()
        known = self.hashes.get(device_id)
        if known is None:
            # 未注册的识别码：允许“查询”，真正的注册只发生在电脑端连接时
            return self.auto_register
        return secrets.compare_digest(known, h)

    # ---------------------------------------------------------- 安全：审计/会话/限速

    def audit(self, event: str, ip: str = "", detail: str = "", level: str = "INFO"):
        """安全审计：写入 security.log，并保留最近 50 条供 /events 查询。
        level 用于日志分级（失败/拒绝/被顶替等标 WARN/ERROR）。"""
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{ip}] {event} {detail}".strip()
        log(level, f"[安全] {line}")
        try:
            with open(LOG_DIR / "security.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        self.events.append({"at": time.strftime("%H:%M:%S"), "ip": ip,
                            "event": event, "detail": detail})
        del self.events[:-50]

    def log_session(self, event: str, device_id: str = "", ip: str = "", detail: str = ""):
        """会话日志：写入 sessions.log（设备/手机每次会话的起止与时长）。
        区别于 security.log（安全事件），这里是完整的连接/会话留痕。"""
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event} device={device_id or '-'} ip={ip or '-'} {detail}".rstrip()
        log("INFO", f"[会话] {line}")
        try:
            with open(LOG_DIR / "sessions.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def touch(self, conn: DeviceConn):
        """收到设备消息时刷新心跳（在事件循环内调用，开销极小）。"""
        conn.last_activity = time.monotonic()

    async def _hb_monitor(self):
        """心跳监控任务：周期性扫描所有设备，超时未活动则强制下线触发重连。"""
        while True:
            await asyncio.sleep(self.hb_interval)
            now = time.monotonic()
            for device_id, conn in list(self.conns.items()):
                if conn.last_activity and (now - conn.last_activity) > self.hb_timeout:
                    self.audit("设备心跳超时，强制下线(触发自动重连)", "",
                               f"{device_id} 静默{self.hb_timeout}s", level="ERROR")
                    self.log_session("设备心跳超时下线", device_id, "",
                                     f"silent={int(now - conn.last_activity)}s")
                    try:
                        await conn.ws.close()
                    except Exception:
                        pass

    def device_auth(self, request) -> str | None:
        """校验手机端请求：会话 Cookie 优先（登录后口令不再进 URL），
        其次 URL id/pass（兼容旧链接）。返回 device_id 或 None。"""
        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            s = self.sessions.get(sid)
            if s and s["exp"] > time.time() and \
                    (s.get("voucher") or self.auth_ok(s["device_id"], s["password"])):
                return s["device_id"]
            self.sessions.pop(sid, None)
        device_id = request.query.get("id", "")
        password = request.query.get("pass", "")
        if self.auth_ok(device_id, password):
            return device_id
        return None

    async def mode(self, request):
        """公开端点：告诉手机端当前是识别码（中继）模式。"""
        return web.json_response({"mode": "relay"})

    async def login(self, request):
        """登录：校验识别码+口令后签发 HttpOnly 会话 Cookie，口令不再留在 URL。"""
        ip = client_ip(request, self.xf_trusted)
        if not self.ratelimit.allow(ip):
            self.audit("登录被限速拒绝(暴力破解防护)", ip, level="ERROR")
            return web.Response(status=429, text="尝试过于频繁，请 5 分钟后再试")
        device_id = request.query.get("id", "")
        password = request.query.get("pass", "")
        code = request.query.get("code", "")
        if request.method == "POST":
            data = await request.post()
            device_id = (data.get("id") or device_id or "").strip()
            password = (data.get("pass") or password or "").strip()
            code = (data.get("code") or code or "").strip()
        # 扫码登录：用电脑终端二维码里的兑换券直接换取会话（券一次性、120s 有效）
        if code and code in self.vouchers:
            v = self.vouchers.pop(code)
            if v["exp"] >= time.time():
                ip = client_ip(request, self.xf_trusted)
                self.audit("扫码登录成功(兑换券)", ip, f"id={v['device_id']}")
                sid = secrets.token_urlsafe(24)
                self.sessions[sid] = {"device_id": v["device_id"], "password": "",
                                      "voucher": True, "exp": time.time() + SESSION_TTL}
                resp = web.Response(status=302, headers={"Location": f"/?id={v['device_id']}"})
                resp.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_TTL, httponly=True,
                                samesite="Lax", path="/")
                return resp
        if not self.auth_ok(device_id, password):
            self.ratelimit.note_fail(ip)
            self.audit("登录失败(识别码或口令错误)", ip, f"id={device_id}", level="WARN")
            return web.Response(status=403, text="识别码或口令错误")
        sid = secrets.token_urlsafe(24)
        self.sessions[sid] = {"device_id": device_id, "password": password,
                              "exp": time.time() + SESSION_TTL}
        self.audit("登录成功", ip, f"id={device_id}")
        resp = web.Response(status=302, headers={"Location": f"/?id={device_id}"})
        resp.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_TTL, httponly=True,
                        samesite="Lax", path="/")
        return resp

    @staticmethod
    def _magic_packet(mac: str) -> bytes:
        """构造 WoL 魔术包：6×0xFF + MAC×16。"""
        mac = mac.replace("-", ":").replace(".", ":")
        parts = [int(x, 16) for x in mac.split(":") if x]
        if len(parts) != 6:
            raise ValueError(f"MAC 地址格式错误: {mac}")
        return b"\xff" * 6 + bytes(parts) * 16

    async def wake(self, request):
        """远程唤醒：手机点"唤醒"，中继向电脑所在局域网广播 WoL 魔术包。
        要求中继与电脑在同一局域网（如中继跑在自家 NAS/路由器上），且配置了 --wol。
        电脑需在 BIOS/网卡启用 Wake-on-LAN。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        mac = self.wol.get(device_id)
        ip = client_ip(request, self.xf_trusted)
        if not mac:
            self.audit("唤醒失败(未配置该设备的MAC)", ip, device_id, level="WARN")
            return web.json_response({"ok": False, "error": "该设备未配置 WoL 唤醒地址"})
        try:
            packet = self._magic_packet(mac)
        except ValueError as exc:
            self.audit("唤醒失败(MAC格式错误)", ip, f"{device_id} {exc}", level="WARN")
            return web.json_response({"ok": False, "error": "MAC 地址格式错误"})
        try:
            # UDP 广播到局域网 9 号端口（WoL 标准端口）
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (self.wol_bcast, 9))
            sock.close()
        except OSError as exc:
            self.audit("唤醒失败(发送UDP失败)", ip, f"{device_id} {exc}", level="ERROR")
            return web.json_response({"ok": False, "error": f"发送失败: {exc}"})
        self.audit("远程唤醒已发送", ip, f"{device_id} MAC={mac} -> {self.wol_bcast}:9")
        return web.json_response({"ok": True, "mac": mac})

    async def events_handler(self, request):
        """最近安全事件（需登录）。"""
        if self.device_auth(request) is None:
            return web.Response(status=403)
        return web.json_response({"ok": True, "events": self.events})

    # ---------------------------------------------------------- 电脑端连接

    async def pc_ws(self, request):
        """电脑主动连接通道。含同识别码多端连接策略：
        - replace（默认）：新连接顶替旧连接（触发重连/换机时可自动接管）
        - reject：设备已在线时拒绝新连接，防止两台电脑抢同一个识别码"""
        device_id = request.query.get("id", "")
        password = request.query.get("pass", "")
        if not self.auth_ok(device_id, password):
            return web.Response(status=403, text="403: 识别码或口令错误")
        if self.auto_register and device_id not in self.hashes:
            self.hashes[device_id] = hashlib.sha256(password.encode()).hexdigest()
            self.audit("设备注册", client_ip(request, self.xf_trusted), device_id)

        old = self.conns.get(device_id)
        if old and self.conn_policy == "reject":
            self.audit("设备连接被拒绝(同识别码已在线)", client_ip(request, self.xf_trusted),
                       device_id, level="WARN")
            self.log_session("设备连接被拒绝", device_id, client_ip(request, self.xf_trusted),
                             f"policy=reject 已有连接{old.uptime}s")
            return web.Response(status=409, text="同识别码设备已在线，连接策略为 reject")

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        if old:
            await old.ws.close()  # replace 策略：新连接顶替旧连接
            self.audit("设备被顶替(同识别码新连接)", client_ip(request, self.xf_trusted),
                       device_id, level="WARN")
            self.log_session("设备被顶替", device_id, client_ip(request, self.xf_trusted), "policy=replace，旧连接被替换")
        conn = DeviceConn(ws)
        self.conns[device_id] = conn
        self.stats["device_sessions"] += 1
        self.stats["devices_seen"].add(device_id)
        self.audit("设备上线", client_ip(request, self.xf_trusted), device_id)
        self.log_session("设备会话开始", device_id, client_ip(request, self.xf_trusted))
        ip = client_ip(request, self.xf_trusted)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self.touch(conn)
                    for q in list(conn.phones):
                        try:
                            q.put_nowait(msg.data)
                        except asyncio.QueueFull:
                            pass  # 慢客户端丢帧
                elif msg.type == WSMsgType.TEXT:
                    self.touch(conn)
                    try:
                        d = json.loads(msg.data)
                        if d.get("t") == "info":
                            conn.info = d
                        elif d.get("t") == "telemetry":
                            conn.telemetry = d.get("data")
                        elif d.get("t") == "loginvoucher" and d.get("code"):
                            # 扫码登录兑换券：电脑端生成的短期一次性码
                            self.vouchers[d["code"]] = {
                                "device_id": device_id,
                                "exp": time.time() + 120,
                            }
                            self.audit("生成扫码登录兑换券", client_ip(request, self.xf_trusted),
                                       device_id)
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            if self.conns.get(device_id) is conn:
                self.conns.pop(device_id, None)
                self.audit("设备下线", "", device_id)
            self.log_session("设备会话结束", device_id, ip,
                             f"duration={int(time.time() - conn.started)}s")
            for q in list(conn.phones):
                self._signal_end(q)
        return ws

    @staticmethod
    def _signal_end(q: asyncio.Queue):
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            q.put_nowait(None)

    # ---------------------------------------------------------- 分块推流

    async def pc_vstream(self, request):
        """电脑分块推流通道：缓存全帧，并广播给所有手机 /vstream 订阅者。"""
        device_id = request.query.get("id", "")
        password = request.query.get("pass", "")
        if not self.auth_ok(device_id, password):
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None:
            return web.Response(status=404, text="device offline")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        conn.vstream_ws = ws
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self.touch(conn)
                    data = msg.data
                    if data and data[0] == 1:
                        conn.last_full = data  # 缓存全帧，供新手机秒开
                    for q in list(conn.vstream_phones):
                        try:
                            q.put_nowait(data)
                        except asyncio.QueueFull:
                            pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            if conn.vstream_ws is ws:
                conn.vstream_ws = None
            for q in list(conn.vstream_phones):
                self._signal_end(q)
        return ws

    async def vstream(self, request):
        """手机分块推流：先发缓存全帧，再实时转发变化块与光标。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None:
            return web.Response(status=503, text="device offline")
        ip = client_ip(request, self.xf_trusted)
        t0 = time.time()
        self.stats["phone_watch"] += 1
        self.log_session("监看会话开始(分块)", device_id, ip)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        q = asyncio.Queue(maxsize=8)
        conn.vstream_phones.add(q)
        try:
            if conn.last_full:
                await ws.send_bytes(conn.last_full)
            while True:
                data = await q.get()
                if data is None:
                    break
                await ws.send_bytes(data)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            conn.vstream_phones.discard(q)
            self.log_session("监看会话结束(分块)", device_id, "",
                             f"duration={int(time.time() - t0)}s")
        return ws

    # ---------------------------------------------------------- 文件传输

    async def pc_file(self, request):
        """电脑文件通道：中继与电脑之间的文件传输专用 WS。
        电脑回复的 text 应答带 tid；binary 为下载数据块（前 4 字节 tid）。
        """
        device_id = request.query.get("id", "")
        password = request.query.get("pass", "")
        if not self.auth_ok(device_id, password):
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None:
            return web.Response(status=404, text="device offline")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        conn.file_ws = ws
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self.touch(conn)
                    try:
                        d = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    tid = d.get("id")
                    q = self.pending.get(tid)
                    if q is not None:
                        await q.put(d)  # 无界队列，不阻塞通道
                elif msg.type == WSMsgType.BINARY:
                    self.touch(conn)
                    data = msg.data
                    if len(data) < 5:
                        continue
                    tid = int.from_bytes(data[:4], "big")
                    q = self.pending.get(tid)
                    if q is not None:
                        await q.put({"kind": "chunk", "data": data[4:]})
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            if conn.file_ws is ws:
                conn.file_ws = None
        return ws

    async def files(self, request):
        """手机端文件浏览：转发给电脑列出目录。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None or conn.file_ws is None:
            return web.Response(status=503, text="device offline")
        tid = self.next_tid()
        q = asyncio.Queue()
        self.pending[tid] = q
        try:
            await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "list",
                                          "path": request.query.get("path", "")})
            resp = await asyncio.wait_for(q.get(), timeout=10)
        except asyncio.TimeoutError:
            return web.json_response({"ok": False, "error": "电脑响应超时"})
        finally:
            self.pending.pop(tid, None)
        if resp.get("kind") == "err":
            return web.json_response({"ok": False, "error": resp.get("msg", "未知错误")})
        return web.json_response({"ok": True, "path": resp.get("path", ""),
                                  "parent": resp.get("parent", ""),
                                  "entries": resp.get("entries", [])})

    async def upload(self, request):
        """手机端上传：把请求体分块转发给电脑写盘。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None or conn.file_ws is None:
            return web.Response(status=503, text="device offline")
        tid = self.next_tid()
        name = (request.query.get("name", "upload.bin") or "upload.bin").replace("\\", "/").split("/")[-1]
        path = request.query.get("path", "")
        sent = 0
        try:
            await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "upload_start",
                                          "name": name, "path": path})
            async for chunk in request.content.iter_chunked(128 * 1024):
                await conn.file_ws.send_bytes(tid.to_bytes(4, "big") + chunk)
                sent += len(chunk)
            await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "upload_end",
                                          "name": name, "path": path})
        except (asyncio.CancelledError, ConnectionResetError):
            try:
                await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "cancel"})
            except Exception:
                pass
            raise
        return web.json_response({"ok": True, "name": name, "size": sent})

    async def download(self, request):
        """手机端下载：向电脑请求文件，把分块转发为 HTTP 响应。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None or conn.file_ws is None:
            return web.Response(status=503, text="device offline")
        tid = self.next_tid()
        q = asyncio.Queue()
        self.pending[tid] = q
        resp = None
        try:
            await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "download",
                                          "path": request.query.get("path", "")})
            header = await asyncio.wait_for(q.get(), timeout=15)
            if header.get("kind") == "err":
                return web.Response(status=404, text=header.get("msg", "file not found"))
            name = header.get("name", "download.bin")
            size = header.get("size", 0)
            resp = web.StreamResponse(headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Length": str(size),
            })
            await resp.prepare(request)
            while True:
                item = await q.get()
                kind = item.get("kind")
                if kind == "dend":
                    break
                if kind == "chunk":
                    await resp.write(item["data"])
                elif kind == "err":
                    break
        except asyncio.TimeoutError:
            try:
                await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "cancel"})
            except Exception:
                pass
            return web.Response(status=504, text="电脑响应超时")
        except asyncio.CancelledError:
            try:
                await conn.file_ws.send_json({"t": "req", "id": tid, "kind": "cancel"})
            except Exception:
                pass
            raise
        finally:
            self.pending.pop(tid, None)
        return resp

    # ---------------------------------------------------------- 手机端

    async def stream(self, request):
        """MJPEG 推流：把电脑推来的帧转发给手机的 <img>。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None:
            return web.Response(status=503, text="device offline")
        ip = client_ip(request, self.xf_trusted)
        t0 = time.time()
        self.stats["phone_watch"] += 1
        self.log_session("监看会话开始(MJPEG)", device_id, ip)

        q = asyncio.Queue(maxsize=2)
        conn.phones.add(q)
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
                data = await q.get()
                if data is None:
                    break  # 电脑断开，结束本次推流
                await resp.write(
                    b"--" + BOUNDARY + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                    + data + b"\r\n"
                )
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            conn.phones.discard(q)
            self.log_session("监看会话结束(MJPEG)", device_id, "",
                             f"duration={int(time.time() - t0)}s")
        return resp

    async def ctl(self, request):
        """手机控制通道：指令转发给对应电脑。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        ip = client_ip(request, self.xf_trusted)
        t0 = time.time()
        self.stats["phone_control"] += 1
        self.log_session("手机会话开始(控制)", device_id, ip)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    conn = self.conns.get(device_id)
                    if conn is not None and not conn.ws.closed:
                        try:
                            await conn.ws.send_str(msg.data)
                        except Exception:
                            pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.log_session("手机会话结束(控制)", device_id, "",
                             f"duration={int(time.time() - t0)}s")
        return ws

    async def info(self, request):
        """设备状态：在线与否、分辨率、画质等（登录后返回 mode/device_id，供手机端识别模式）。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        if conn is None:
            return web.json_response({"online": False, "mode": "relay", "device_id": device_id})
        return web.json_response({"online": True, "mode": "relay", "device_id": device_id,
                                  "alive": conn.alive,
                                  "uptime": conn.uptime,
                                  "last_seen_sec": round(conn.last_seen_sec, 1),
                                  **conn.info})

    async def devices(self, request):
        """运营/排查仪表盘：列出当前所有在线设备的心跳与连接状态（需登录）。"""
        if self.device_auth(request) is None:
            return web.Response(status=403)
        out = []
        now = time.monotonic()
        for did, conn in list(self.conns.items()):
            alive_now = (now - conn.last_activity) <= self.hb_timeout
            out.append({
                "device_id": did,
                "online": True,
                "alive": alive_now,
                "uptime": conn.uptime,
                "last_seen_sec": round(now - conn.last_activity, 1),
                "preset": conn.info.get("preset", ""),
                "platform": conn.info.get("platform", ""),
                "w": conn.info.get("w", 0),
                "h": conn.info.get("h", 0),
            })
        return web.json_response({
            "ok": True,
            "policy": self.conn_policy,
            "hb_timeout": self.hb_timeout,
            "count": len(out),
            "devices": out,
            "stats": {
                "device_sessions": self.stats["device_sessions"],
                "phone_control": self.stats["phone_control"],
                "phone_watch": self.stats["phone_watch"],
                "devices_seen": len(self.stats["devices_seen"]),
            },
        })

    async def index(self, request):
        # 禁止缓存页面：避免手机端一直加载旧版客户端代码
        return web.FileResponse(BASE_DIR / "web" / "index.html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})

    async def doctor(self, request):
        """手机端自检仪表盘页面（数据来自 /status）。"""
        return web.FileResponse(BASE_DIR / "web" / "doctor.html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})

    async def manifest(self, request):
        """PWA manifest（手机"添加到主屏幕"用）。"""
        return web.FileResponse(BASE_DIR / "web" / "manifest.json",
                                headers={"Content-Type": "application/manifest+json"})

    async def icon(self, request):
        """PWA 图标。"""
        name = request.match_info.get("name", "")
        path = (BASE_DIR / "web" / "icons" / name).resolve()
        if path.parent != (BASE_DIR / "web" / "icons").resolve() or not path.is_file():
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Type": "image/png"})

    async def status(self, request):
        """设备自检报告：在线状态 + 电脑端上报的遥测。"""
        device_id = self.device_auth(request)
        if device_id is None:
            return web.Response(status=403)
        conn = self.conns.get(device_id)
        base = {
            "mode": "relay",
            "ok": conn is not None,
            "uptime": int(time.monotonic() - self.start),
            "device_id": device_id,
            "online": conn is not None,
        }
        if conn is not None:
            base["info"] = conn.info
            base["telemetry"] = conn.telemetry
            base["alive"] = (time.monotonic() - conn.last_activity) <= self.hb_timeout
            base["uptime"] = conn.uptime
            base["last_seen_sec"] = round(conn.last_seen_sec, 1)
            base["stats"] = {
                "device_sessions": self.stats["device_sessions"],
                "phone_control": self.stats["phone_control"],
                "phone_watch": self.stats["phone_watch"],
            }
        return web.json_response(base)


def parse_devices(text: str) -> dict:
    """解析 --devices "id1:pass1,id2:pass2"。"""
    out = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            k, v = item.split(":", 1)
            out[k.strip()] = v.strip()
        else:
            out[item] = ""
    return out


def main():
    parser = argparse.ArgumentParser(description="FreeRemote 中继服务器（识别码配对）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", 9090)),
                        help="监听端口（默认 $PORT 或 9090；Render/Fly 会注入 PORT）")
    parser.add_argument("--devices",
                        default=os.environ.get("RELAY_DEVICES", ""),
                        help='预注册设备，如 "ABC123:mypass,DEF456:pass2"（可用环境变量 RELAY_DEVICES）')
    parser.add_argument("--strict", action="store_true",
                        help="只允许 --devices 里登记的识别码连接（默认允许自动注册，可用环境变量 RELAY_STRICT=1）")
    parser.add_argument("--conn-policy", choices=["replace", "reject"], default="replace",
                        help="同识别码多端连接策略：replace=新连接顶替旧连接(默认)；reject=设备在线时拒绝新连接")
    parser.add_argument("--hb-timeout", type=int, default=90,
                        help="设备心跳超时（秒），超过视为断线并强制下线触发重连（默认 90）")
    parser.add_argument("--hb-interval", type=int, default=10,
                        help="心跳扫描间隔（秒，默认 10）")
    parser.add_argument("--wol", default=os.environ.get("RELAY_WOL", ""),
                        help='远程唤醒映射，如 "TEST01:AA-BB-CC-DD-EE-FF"（识别码:MAC，可多个用逗号分隔；可用环境变量 RELAY_WOL）')
    parser.add_argument("--wol-bcast", default="255.255.255.255",
                        help="WoL 广播地址（默认全子网广播 255.255.255.255；跨网段可填如 192.168.1.255）")
    parser.add_argument("--xf-trusted", action="store_true",
                        help="信任 X-Forwarded-For（部署在 Render/Fly.io 等 TLS 终结代理之后时开启，"
                             "否则限速/审计/白名单会拿到代理 IP 而非真实用户 IP）")
    args = parser.parse_args()
    if os.environ.get("RELAY_STRICT", "").lower() in ("1", "true", "yes", "on"):
        args.strict = True

    async def on_startup(app):
        app["relay"].hb_task = asyncio.create_task(app["relay"]._hb_monitor())

    async def on_shutdown(app):
        if app["relay"].hb_task:
            app["relay"].hb_task.cancel()

    auto = not args.strict
    relay = Relay(parse_devices(args.devices), auto_register=auto,
                  conn_policy=args.conn_policy,
                  hb_timeout=args.hb_timeout, hb_interval=args.hb_interval,
                  xf_trusted=args.xf_trusted, wol=parse_devices(args.wol),
                  wol_bcast=args.wol_bcast)
    app = web.Application()
    app["relay"] = relay
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", relay.index)
    app.router.add_get("/mode", relay.mode)
    app.router.add_get("/login", relay.login)
    app.router.add_post("/login", relay.login)
    app.router.add_get("/events", relay.events_handler)
    app.router.add_get("/info", relay.info)
    app.router.add_get("/stream", relay.stream)
    app.router.add_get("/ctl", relay.ctl)
    app.router.add_get("/pc/ws", relay.pc_ws)
    app.router.add_get("/pc/file", relay.pc_file)
    app.router.add_get("/pc/vstream", relay.pc_vstream)
    app.router.add_get("/vstream", relay.vstream)
    app.router.add_get("/status", relay.status)
    app.router.add_get("/devices", relay.devices)
    app.router.add_post("/wake", relay.wake)
    app.router.add_get("/doctor", relay.doctor)
    app.router.add_get("/files", relay.files)
    app.router.add_post("/upload", relay.upload)
    app.router.add_get("/download", relay.download)
    app.router.add_get("/manifest.json", relay.manifest)
    app.router.add_get("/icons/{name}", relay.icon)

    out("=" * 58)
    out(" FreeRemote 中继服务器已启动（识别码配对）")
    out(f"   端口     : {args.port}")
    out(f"   预注册   : {args.devices or '(无，允许自动注册)'}")
    out(f"   连接策略 : {args.conn_policy}（同识别码多端）  心跳超时: {args.hb_timeout}s")
    if args.xf_trusted:
        out("   XFF      : 已信任 X-Forwarded-For（部署在 TLS 终结代理之后，限速/审计按真实 IP）")
    out("   手机访问 : https://<本服务器域名>/  （输入识别码+口令登录，口令不进 URL）")
    out("   电脑连接 : python server.py --relay wss://<本服务器域名> --id 识别码 --password 口令")
    out(f"   唤醒     : {args.wol or '(未配置 --wol，无远程唤醒)'}")
    out("   监控     : 心跳监控 + 会话日志(sessions.log) + 审计(security.log) + 设备上下线告警")
    out(f"   日志     : {LOG_FILE.relative_to(BASE_DIR)}（UTF-8，错误行含 [ERROR]，可 grep）")
    out("=" * 58)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
