#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeRemote —— 手机浏览器远程控制电脑（监看 + 控制）

在电脑上运行：  python server.py [--port 8080] [--token xxxx]
手机浏览器打开：http://<电脑IP>:8080/?token=xxxx

原理：
  - mss 采集屏幕 -> Pillow 缩放/JPEG 编码 -> MJPEG 推流（手机 <img> 直接播放）
  - WebSocket 接收手机触摸/按键指令 -> pyautogui 注入到系统
"""

import argparse
import asyncio
import io
import json
import os
import random
import secrets
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web, WSMsgType
import mss
from PIL import Image

import pyautogui
import pyperclip

BASE_DIR = Path(__file__).resolve().parent

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"


def log(level: str, msg: str):
    """统一日志：控制台打印 [时间] [级别] 消息，同时强制 UTF-8 落盘 server.log。
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

# 画质档位：名称 -> (缩放比例, JPEG 质量)
QUALITY_PRESETS = {
    "low": (0.35, 45),    # 流畅：适合弱网
    "mid": (0.6, 65),     # 均衡（默认）
    "high": (1.0, 88),    # 高清：局域网
}

BOUNDARY = b"frame"

# 口令词表：全部小写、短小、无易混淆字母，方便手输与口头转述
TOKEN_WORDS = (
    "maple otter luna nova pixel mango tiny echo sunny comet lucky "
    "fox river cloud happy panda kiwi amber noble cactus cedar cherry "
    "cobra coral daisy delta drift ember falcon flint grove hazel "
    "iris jade lemon lotus meadow neon olive pearl raven solar tulip "
    "vivid wren zebra anchor brush camel dune fable garnet herb jolly "
    "koala mint nutmeg opal quail reef sage thyme"
).split()


def gen_token() -> str:
    """直观口令：3 个小写单词 + 2 位数字，如 maple-otter-luna-724。

    组合约 75^3 * 90 ≈ 2^30，配合登录限速足够安全，且比随机字符串
    好念、好记、好手输（口头转述也不容易错）。
    """
    words = "-".join(secrets.choice(TOKEN_WORDS) for _ in range(3))
    return f"{words}-{secrets.randbelow(90) + 10}"


def save_token_file(token: str, expire) -> None:
    """保存口令到 token.txt；expire=None 表示永久。内容无变化时不写盘（避免触发热更新）。"""
    cf = BASE_DIR / "token.txt"
    content = token if not expire else "{}\n{}".format(token, expire)
    try:
        if cf.exists() and cf.read_text(encoding="utf-8").strip() == content.strip():
            return
    except OSError:
        pass
    cf.write_text(content, encoding="utf-8")


def load_token_file():
    """读 token.txt → (口令, 过期时间戳或 None=永久)。

    兼容历史格式（纯口令 / 口令+时间戳 / 旧随机串）。已过期时不换口令，
    只把有效期延长 1 年——口令不变，手机书签永远不用更新。
    """
    cf = BASE_DIR / "token.txt"
    if not cf.exists():
        return None, None
    try:
        lines = cf.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None, None
    if not lines or not lines[0].strip():
        return None, None
    token = lines[0].strip()
    expire = None
    if len(lines) >= 2:
        try:
            expire = int(lines[1].strip())
        except ValueError:
            expire = None
        if expire is not None and time.time() > expire:
            expire = int(time.time()) + 365 * 86400  # 已过期：同口令延长 1 年
    save_token_file(token, expire)  # 归一化存储（永久口令只留一行）
    return token, expire


def is_screen_locked() -> bool:
    """Windows 下检测屏幕是否锁定（锁屏时无法注入输入）。启发式，仅作提示。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not h:
            return True
        u.CloseDesktop(h)
        return False
    except Exception:
        return False


class HealthMonitor:
    """运行时健康监控 + 自动补救（自检/自我审查/自愈）。"""

    def __init__(self, target_fps: int):
        self.target_fps = target_fps
        self.start = time.monotonic()
        self.capture_ok = 0
        self.capture_fail = 0
        self.consecutive_fail = 0
        self.last_err = None
        self.reconnects = 0
        self.fps_ema = 0.0
        self.last_frame_t = None
        self.degraded = False
        self.remedies = []  # [{at, action}] 自动补救记录
        # GIL 卡顿检测：采集耗时异常的样本（dt 超过目标周期 2.5 倍）
        self.slow_notes: list[float] = []
        self._gil_warned_at = 0.0
        self.capture_pauses = 0
        self.paused_until = 0.0
        self._expected_dt = 1.0 / max(target_fps, 1) * 2.5

    # ---- 采集统计 ----
    def note_ok(self):
        now = time.monotonic()
        if self.last_frame_t:
            dt = now - self.last_frame_t
            inst = 1.0 / dt if dt > 0 else 0.0
            self.fps_ema = self.fps_ema * 0.8 + inst * 0.2 if self.fps_ema else inst
            if dt > self._expected_dt:  # 本帧采集耗时异常（GIL 被占用的典型信号）
                self.slow_notes.append(now)
                cutoff = now - 4.0
                self.slow_notes = [t for t in self.slow_notes if t > cutoff]
        self.last_frame_t = now
        self.capture_ok += 1
        self.consecutive_fail = 0

    def note_fail(self, exc):
        self.capture_fail += 1
        self.consecutive_fail += 1
        self.last_err = str(exc)[:120]

    def note_reconnect(self):
        self.reconnects += 1

    # ---- GIL 争用自保（minimize 重型应用 / 编译 / 压缩时） ----
    def check_gil_pause(self, streamer):
        """无观看者且 GIL 持续卡顿 → 暂停采集 5 秒，把 CPU 让给前台应用。"""
        now = time.monotonic()
        if now < self.paused_until:
            return True  # 暂停中
        if len(self.slow_notes) >= 3 and (streamer is None or not self._has_viewers(streamer)):
            self.paused_until = now + 5.0
            self.capture_pauses += 1
            self.slow_notes = []
            if now - self._gil_warned_at > 30:
                self._gil_warned_at = now
                self.add_remedy("检测到系统繁忙且无观看者，暂停屏幕采集 5 秒让出 CPU")
            return True
        return False

    def _has_viewers(self, streamer) -> bool:
        fs = getattr(streamer, "_owner_fs", None)
        bc = getattr(streamer, "_owner_bc", None)
        return bool((fs and fs.subs) or (bc and bc.subs))

    def add_remedy(self, action: str):
        self.remedies.append({"at": time.strftime("%H:%M:%S"), "action": action})
        log("WARN", f"自愈：{action}")

    # ---- 自动补救 ----
    def maybe_heal(self, streamer):
        """采集连续失败→重建采集器；实际帧率过低→自动降一档画质。"""
        if streamer is None:
            return
        if self.consecutive_fail >= 5:
            streamer.reinit()
            self.consecutive_fail = 0
            self.add_remedy("屏幕采集连续失败，已自动重建采集器")
        if (self.fps_ema and self.fps_ema < self.target_fps * 0.5
                and not self.degraded and streamer.preset != "low"):
            order = ["high", "mid", "low"]
            idx = order.index(streamer.preset)
            if idx < len(order) - 1:
                streamer.set_preset(order[idx + 1])
                self.degraded = True
                self.add_remedy(f"实际帧率 {self.fps_ema:.1f}fps 低于目标 {self.target_fps}fps，"
                                f"已自动降为「{streamer.preset}」画质")

    # ---- 自检报告（供 /status 与手机端自检页） ----
    def report(self, streamer=None, mode="lan", no_auth=False, token_len=0, ws_ok=None):
        total = self.capture_ok + self.capture_fail
        fps_now = round(self.fps_ema, 1)
        checks = []

        cap_ok = total == 0 or self.consecutive_fail < 5
        checks.append({
            "id": "capture", "name": "屏幕采集", "ok": cap_ok,
            "detail": f"成功 {self.capture_ok} 次 / 失败 {self.capture_fail} 次"
                       + (f"（最近错误：{self.last_err}）" if self.last_err else ""),
            "remedy": "macOS：系统设置→隐私与安全性→屏幕录制 授权；"
                       "Windows：检查是否锁屏/远程桌面会话已断开",
        })
        fps_ok = total == 0 or (self.fps_ema >= self.target_fps * 0.5)
        checks.append({
            "id": "fps", "name": "推流帧率", "ok": fps_ok,
            "detail": f"实际 {fps_now} fps / 目标 {self.target_fps} fps"
                       + ("（已自动降画质）" if self.degraded else ""),
            "remedy": "在手机端切到「流畅」画质，或调低 --fps；检查网络带宽",
        })
        checks.append({
            "id": "conn", "name": "控制通道", "ok": ws_ok is not False,
            "detail": "已连接" if ws_ok else (("中继已连接" if mode == "relay" else "局域网模式") if ws_ok is None else "已断开"),
            "remedy": "检查电脑与手机是否同一网络；Windows 防火墙是否放行端口；中继是否可达",
        })
        checks.append({
            "id": "quality", "name": "画质档位", "ok": not self.degraded,
            "detail": f"当前 {streamer.preset if streamer else '?'}"
                       + ("（已自动降档）" if self.degraded else ""),
            "remedy": "网络恢复后可在手机端手动切回「均衡/高清」",
        })
        if is_screen_locked():
            checks.append({
                "id": "lock", "name": "屏幕状态", "ok": False,
                "detail": "检测到屏幕可能已锁定", "remedy": "先解锁电脑屏幕再操作（锁屏时无法注入）",
            })

        warnings = []
        if no_auth:
            warnings.append("口令校验已关闭（--no-auth），仅限完全可信的局域网")
        if token_len and token_len < 8:
            warnings.append(f"当前口令仅 {token_len} 位，建议至少 8 位（--token）")
        if self.degraded:
            warnings.append("已自动降画质，可手动切回更高画质")

        return {
            "ok": all(c["ok"] for c in checks),
            "mode": mode,
            "uptime": int(time.monotonic() - self.start),
            "fps_target": self.target_fps,
            "fps_actual": fps_now,
            "preset": streamer.preset if streamer else "",
            "frame": list(streamer.frame_size) if streamer else None,
            "reconnects": self.reconnects,
            "warnings": warnings,
            "checks": checks,
            "remedies": list(self.remedies[-10:]),
        }


def _qr_text(qr) -> str:
    """把 qrcode 矩阵渲染成纯 ASCII（# 与空格），GBK 终端也能正常显示扫码。"""
    rows = []
    for line in qr.get_matrix():
        rows.append("".join("##" if cell else "  " for cell in line))
    return "\n".join(rows)


def print_qr(url: str, title: str = "用手机相机扫这个二维码"):
    """终端打印二维码 + 链接（依赖 qrcode，可选）；同时伴写日志文件（UTF-8）。"""
    out("")
    out("=" * 58)
    out(f" {title}")
    out(f" {url}")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        out(_qr_text(qr))
    except Exception:
        pass
    out("=" * 58)


def _is_private_lan(ip: str) -> bool:
    """是否私网局域网地址（10.x / 172.16-31.x / 192.168.x），排除回环。"""
    try:
        part = [int(x) for x in ip.split(".")]
        if len(part) != 4:
            return False
        a, b = part[0], part[1]
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
    except Exception:
        return False


def lan_ips() -> tuple[str, list[str]]:
    """返回 (首选IP, 全部候选IP列表)。
    首选 = 默认路由网卡（最可能手机能连）；候选 = 主机名解析出的所有局域网 IPv4。
    """
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if _is_private_lan(ip):
                ips.add(ip)
    except Exception:
        pass
    primary = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
        if _is_private_lan(primary):
            ips.add(primary)
    except Exception:
        pass
    ordered = [primary] + [ip for ip in sorted(ips) if ip != primary]
    return primary, ordered or ["127.0.0.1"]


def tailscale_ips() -> list[str]:
    """探测本机 Tailscale 的 100.x 地址（手机跨网直连用，比局域网 IP 更通用）。"""
    import shutil
    exe = shutil.which("tailscale")
    if not exe:
        for p in (r"C:\Program Files\Tailscale\tailscale.exe",
                  r"D:\Program Files\Tailscale\tailscale.exe",
                  r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
            if os.path.exists(p):
                exe = p
                break
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True,
                             timeout=4).stdout
        return [ip.strip() for ip in out.splitlines() if ip.strip()]
    except Exception:
        return []


class Streamer:
    """屏幕采集 + 编码。阻塞操作由调用方放入线程池执行。"""

    def __init__(self, monitor_idx: int, preset: str, fps: int, monitor: HealthMonitor | None = None):
        self.sct = mss.MSS()
        monitors = self.sct.monitors
        if monitor_idx < 1 or monitor_idx >= len(monitors):
            monitor_idx = 1
        self._monitor_idx = monitor_idx
        self.monitor = self.sct.monitors[monitor_idx]
        self.fps = max(1, min(fps, 30))
        self.user_scale = None  # 手机端设置的额外缩放（None=跟随画质档位）
        self.set_preset(preset)
        self.health = monitor  # 健康监控（自检/自愈）
        self._fallback = None  # 采集失败时发送的占位图

    def reinit(self):
        """采集连续失败时重建采集器（自愈）。"""
        try:
            self.sct.close()
        except Exception:
            pass
        try:
            self.sct = mss.MSS()
            monitors = self.sct.monitors
            if 1 <= self._monitor_idx < len(monitors):
                self.monitor = monitors[self._monitor_idx]
            self._fallback = None
        except Exception as exc:
            log("ERROR", f"重建采集器失败：{exc}")

    def set_preset(self, preset: str):
        if preset not in QUALITY_PRESETS:
            preset = "mid"
        self.preset = preset
        base_scale, self.quality = QUALITY_PRESETS[preset]
        # 手机端自定义分辨率优先；未设置时跟随画质档位
        self.scale = self.user_scale if self.user_scale else base_scale

    def set_resolution(self, scale):
        """手机端自定义推流分辨率：scale=0.25~2.0；None=恢复跟随画质档位。"""
        if scale is None:
            self.user_scale = None
        else:
            self.user_scale = max(0.2, min(float(scale), 2.0))
        self.set_preset(self.preset)

    @property
    def frame_size(self):
        return (
            int(self.monitor["width"] * self.scale),
            int(self.monitor["height"] * self.scale),
        )

    def frame_to_screen(self, x: float, y: float):
        """把手机端(缩放后帧)坐标映射为真实屏幕坐标。"""
        m = self.monitor
        return m["left"] + x / self.scale, m["top"] + y / self.scale

    def capture_rgb(self):
        """采集一帧，返回 (RGB 原始字节, 宽, 高)，供分块增量编码使用。"""
        try:
            img = self.sct.grab(self.monitor)
            pil = Image.frombytes("RGB", img.size, img.rgb)
            w, h = self.frame_size
            if (w, h) != pil.size:
                # BILINEAR：相比 LANCZOS 快一倍，适合实时推流
                pil = pil.resize((w, h), Image.BILINEAR)
            if self.health:
                self.health.note_ok()
            return pil.tobytes(), w, h
        except Exception as exc:
            log("ERROR", f"采集失败：{exc}")
            if self.health:
                self.health.note_fail(exc)
            return b"", 0, 0

    def capture(self) -> bytes:
        """采集一帧并编码为 JPEG 字节；失败返回占位图并计入健康统计。"""
        try:
            img = self.sct.grab(self.monitor)
            pil = Image.frombytes("RGB", img.size, img.rgb)
            w, h = self.frame_size
            if (w, h) != pil.size:
                pil = pil.resize((w, h), Image.BILINEAR)
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=self.quality)
            if self.health:
                self.health.note_ok()
            return buf.getvalue()
        except Exception as exc:
            log("ERROR", f"采集失败：{exc}")
            if self.health:
                self.health.note_fail(exc)
            return self._fallback or self._make_fallback()

    def _make_fallback(self) -> bytes:
        img = Image.new("RGB", (640, 360), (20, 22, 26))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=60)
        self._fallback = buf.getvalue()
        return self._fallback


class Broadcaster:
    """单个采集循环，向所有订阅者广播同一帧，避免多客户端重复采集。"""

    def __init__(self, streamer: Streamer):
        self.streamer = streamer
        self.subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._loop = None

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        while True:
            t0 = self._loop.time()
            try:
                # 系统繁忙且无观看者时暂停采集，把 CPU 让给前台应用
                if self.streamer.health and self.streamer.health.check_gil_pause(self.streamer):
                    await _pace(self._loop, 1.0, t0)
                    continue
                data = await self._loop.run_in_executor(None, self.streamer.capture)
                if data:
                    if self.streamer.health:
                        self.streamer.health.maybe_heal(self.streamer)  # 自愈
                    for q in list(self.subs):
                        try:
                            q.put_nowait(data)
                        except asyncio.QueueFull:
                            pass  # 慢客户端丢帧
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log("ERROR", f"推流错误：{exc}")
            await _pace(self._loop, self.streamer.fps, t0)

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=2)
        self.subs.add(q)
        if self._task is None:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subs.discard(q)
        if not self.subs and self._task:
            self._task.cancel()
            self._task = None


class FrameSource:
    """共享采集源：广播 (RGB字节, 宽, 高) 给分块渲染客户端。有订阅者才采集（懒启动）。"""

    def __init__(self, streamer: Streamer):
        self.streamer = streamer
        self.subs: set[asyncio.Queue] = set()
        self.loop = None
        self.task = None

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=2)
        self.subs.add(q)
        if self.task is None:
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.create_task(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subs.discard(q)
        if not self.subs and self.task:
            self.task.cancel()
            self.task = None

    async def _run(self):
        while True:
            t0 = self.loop.time()
            try:
                # 系统繁忙且无观看者时暂停采集，把 CPU 让给前台应用
                if self.streamer.health and self.streamer.health.check_gil_pause(self.streamer):
                    await _pace(self.loop, 1.0, t0)
                    continue
                rgb, w, h = await self.loop.run_in_executor(None, self.streamer.capture_rgb)
                if rgb:
                    for q in list(self.subs):
                        try:
                            q.put_nowait((rgb, w, h))
                        except asyncio.QueueFull:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log("ERROR", f"分块采集错误：{exc}")
            await _pace(self.loop, self.streamer.fps, t0)


class BlockEncoder:
    """分块增量编码：把帧切成 BLOCK×BLOCK 小块，只编码变化区域，显著降低带宽与延迟。
    消息格式（二进制）：
      0x01 全帧: [type][2B w][2B h][2B jpegLen][jpeg][2B 光标x][2B 光标y]
      0x02 分块: [type][2B count][每块: 2B 块号][2B len][jpeg]...[2B 光标x][2B 光标y]
      0x04 光标: [type][2B x][2B y]
    """

    BLOCK = 64
    FULL = 0x01
    BLOCKS = 0x02
    CURSOR = 0x04

    def __init__(self, streamer: Streamer):
        self.streamer = streamer
        self.prev = {}
        self.crcs = {}  # idx -> 上次发送的块 CRC（压缩结果等价即跳过）
        self.prev_w = 0
        self.prev_h = 0
        self.last_cursor = None
        self.full_threshold = 0.6  # 变化块超过 60% 时改发全帧（更划算）

    def grid(self, w, h):
        cols = (w + self.BLOCK - 1) // self.BLOCK
        rows = (h + self.BLOCK - 1) // self.BLOCK
        return cols, rows

    def block_bytes(self, rgb, w, h, row, col):
        y0, y1 = row * self.BLOCK, min(row * self.BLOCK + self.BLOCK, h)
        x0, x1 = col * self.BLOCK, min(col * self.BLOCK + self.BLOCK, w)
        stride = w * 3
        if x1 - x0 == w:  # 整行连续内存 → 一次切片，不再逐行 join
            return rgb[y0 * stride: y1 * stride]
        return b"".join(rgb[y * stride + x0 * 3: y * stride + x1 * 3] for y in range(y0, y1))

    def snapshot(self, rgb, w, h):
        cols, rows = self.grid(w, h)
        self.prev = {}
        self.crcs = {}
        for row in range(rows):
            for col in range(cols):
                b = self.block_bytes(rgb, w, h, row, col)
                self.prev[row * cols + col] = b
                self.crcs[row * cols + col] = zlib.crc32(b)
        self.prev_w, self.prev_h = w, h

    def encode_full(self, rgb, w, h, cx, cy) -> bytes:
        pil = Image.frombytes("RGB", (w, h), rgb)
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=self.streamer.quality)
        jpeg = buf.getvalue()
        self.last_cursor = (cx, cy)
        # 全帧 JPEG 可能超过 64KB，长度用 4 字节
        return (b"\x01" + struct.pack(">HHI", w, h, len(jpeg))
                + jpeg + struct.pack(">HH", cx, cy))

    def encode_block(self, rgb, w, h, row, col) -> bytes:
        y0, y1 = row * self.BLOCK, min(row * self.BLOCK + self.BLOCK, h)
        x0, x1 = col * self.BLOCK, min(col * self.BLOCK + self.BLOCK, w)
        return self.encode_block_bytes(self.block_bytes(rgb, w, h, row, col), row, col)

    def encode_block_bytes(self, block: bytes, row, col) -> bytes:
        """把已切好的块字节编码为 JPEG（diff 路径复用，避免二次切片）。"""
        y1 = min(row * self.BLOCK + self.BLOCK, self.prev_h)
        x1 = min(col * self.BLOCK + self.BLOCK, self.prev_w)
        pil = Image.frombytes("RGB", (x1 - col * self.BLOCK, y1 - row * self.BLOCK), block)
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=self.streamer.quality)
        return buf.getvalue()

    def diff(self, rgb, w, h, cx, cy) -> list[bytes]:
        """对比上一帧，返回本次要发送的消息列表（可为空）。"""
        if not self.prev or w != self.prev_w or h != self.prev_h:
            self.snapshot(rgb, w, h)
            return [self.encode_full(rgb, w, h, cx, cy)]
        cols, rows = self.grid(w, h)
        changed = []
        prev = self.prev
        for row in range(rows):
            for col in range(cols):
                idx = row * cols + col
                b = self.block_bytes(rgb, w, h, row, col)
                if prev.get(idx) != b:
                    prev[idx] = b
                    changed.append(idx)
        total = cols * rows
        if not changed:
            if (cx, cy) != self.last_cursor:
                self.last_cursor = (cx, cy)
                return [b"\x04" + struct.pack(">HH", cx, cy)]
            return []
        if len(changed) > total * self.full_threshold:
            self.snapshot(rgb, w, h)
            return [self.encode_full(rgb, w, h, cx, cy)]
        payload = bytearray(b"\x02\x00\x00")  # 块数占位，编码后回填真实数量
        sent = 0
        for idx in changed:
            row, col = divmod(idx, cols)
            b = prev[idx]  # diff 循环已算好块字节，直接复用
            if zlib.crc32(b) == self.crcs.get(idx):  # 压缩等价但字节不同的块跳过
                continue
            self.crcs[idx] = zlib.crc32(b)
            jpeg = self.encode_block_bytes(b, row, col)
            payload += struct.pack(">HH", idx, len(jpeg)) + jpeg
            sent += 1
        self.last_cursor = (cx, cy)
        if not sent:
            # 全部块都是压缩等价抖动 → 只发 6 字节光标消息（协议：块数为 0 不合法）
            return [b"\x04" + struct.pack(">HH", cx, cy)]
        struct.pack_into(">H", payload, 1, sent)
        payload += struct.pack(">HH", cx, cy)
        return [bytes(payload)]


def cursor_frame(streamer: Streamer):
    """当前鼠标位置 → 帧坐标（用于客户端本地绘制光标）。"""
    try:
        x, y = pyautogui.position()
    except Exception:
        return 0, 0
    m = streamer.monitor
    return int((x - m["left"]) * streamer.scale), int((y - m["top"]) * streamer.scale)


# ---------------------------------------------------------------- 控制指令

def _is_ascii_printable(s: str) -> bool:
    """可打印 ASCII（字母/数字/空格/常见符号）→ 可直接逐键敲出，不依赖剪贴板。"""
    return bool(s) and all(not (ord(c) < 0x20 or ord(c) > 0x7E) for c in s)


def _paste_keys():
    """粘贴组合键：macOS 用 Cmd+V，其它平台用 Ctrl+V。"""
    return ["command", "v"] if IS_MAC else ["ctrl", "v"]


def _with_mods(mods, fn):
    """按住修饰键执行动作（如 Ctrl+点击）。"""
    for k in mods:
        pyautogui.keyDown(k)
    try:
        fn()
    finally:
        for k in reversed(mods):
            pyautogui.keyUp(k)


_MOD_KEYS = ("ctrl", "alt", "shift", "win")


def force_release_all_mods(reason: str = ""):
    """强制释放所有修饰键，防"卡键"。

    场景：手机断线/息屏时 Ctrl 或 Win 正被按住 -> 电脑端键位卡死，
    之后手机的一切点击都变成 Ctrl+点击/Win+点击，表现为"全部失灵"。
    在控制连接断开时调用（websocket 心跳 30s 内必发现断线）。
    """
    for k in _MOD_KEYS:
        try:
            pyautogui.keyUp(k)
        except Exception:
            pass
    if reason:
        log("INFO", f"已强制释放修饰键（{reason}），防卡键")


def apply_command(streamer: Streamer, cmd: dict):
    """执行一条来自手机的指令。"""
    t = cmd.get("t")
    mods = [k for k in cmd.get("mods", []) if isinstance(k, str)]
    try:
        if t == "move":
            x, y = streamer.frame_to_screen(cmd["x"], cmd["y"])
            pyautogui.moveTo(x, y, duration=0)

        elif t == "click":
            x, y = streamer.frame_to_screen(cmd["x"], cmd["y"])
            button = cmd.get("button", "left")
            clicks = cmd.get("count", 1)
            _with_mods(mods, lambda: pyautogui.click(x, y, button=button, clicks=clicks))

        elif t == "scroll":
            dy, dx = cmd.get("dy", 0), cmd.get("dx", 0)
            _with_mods(
                mods,
                lambda: (
                    pyautogui.scroll(dy) if dy else None,
                    pyautogui.hscroll(dx) if dx else None,
                ),
            )

        elif t == "keydown":
            _with_mods(mods, lambda: pyautogui.keyDown(cmd["key"]))
        elif t == "keyup":
            _with_mods(mods, lambda: pyautogui.keyUp(cmd["key"]))
        elif t == "press":
            _with_mods(mods, lambda: pyautogui.press(cmd["key"]))

        elif t == "combo":
            # 例如 ["ctrl","alt","del"]：按住前面的键，按下最后一个
            keys = cmd["keys"]
            if keys:
                for k in keys[:-1]:
                    pyautogui.keyDown(k)
                try:
                    pyautogui.press(keys[-1])
                finally:
                    for k in reversed(keys[:-1]):
                        pyautogui.keyUp(k)

        elif t == "text":
            text = cmd.get("text", "")
            if text:
                if _is_ascii_printable(text):
                    # 英文/符号：电脑端直接逐键敲出（不需要剪贴板，macOS/弱网更稳）
                    try:
                        pyautogui.typewrite(text, interval=0.01)
                    except Exception:
                        pyperclip.copy(text)
                        pyautogui.hotkey(*(_paste_keys()))
                else:
                    # 中文等：复制到剪贴板后 Ctrl/Cmd+V 粘贴
                    pyperclip.copy(text)
                    pyautogui.hotkey(*_paste_keys())

        elif t == "releasekeys":
            # 手机端"解锁键盘"：修饰键疑似卡住时一键恢复（页面失灵自救）
            force_release_all_mods("手机端请求")

        elif t == "setpreset":
            # 手机端切换画质（中继模式下由电脑端本地编码）
            streamer.set_preset(cmd.get("preset", "mid"))
        elif t == "setres":
            # 手机端自定义推流分辨率（"auto"=恢复跟随画质档位）
            v = cmd.get("scale", None)
            if v in (None, "auto", ""):
                streamer.set_resolution(None)
            else:
                streamer.set_resolution(float(v))

        else:
            log("WARN", f"未知指令：{cmd}")
    except Exception as exc:
        log("ERROR", f"指令失败 [{t}]：{exc}")


# ---------------------------------------------------------------- 文件互传

def default_share_dir() -> str:
    """自动选择「不在系统盘(C:)」的专用文件夹，方便找文件；没有其它盘则退回下载目录。"""
    if os.name == "nt":
        try:
            sys_letter = os.path.splitdrive(os.getcwd())[0][0].upper()
        except Exception:
            sys_letter = "C"
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            if letter == sys_letter:
                continue
            drive = f"{letter}:"
            if os.path.exists(drive + os.sep):
                p = drive + os.sep + "FreeRemoteFiles"
                try:
                    os.makedirs(p, exist_ok=True)
                    return p
                except OSError:
                    continue
    return str(Path.home() / "Downloads")


def safe_file_name(name: str) -> str:
    """去除路径分隔，只保留文件名。"""
    name = os.path.basename(name.replace("\\", "/")).strip()
    return name or "upload.bin"


def resolve_fs_path(root: str, p: str) -> str:
    """把手机端路径参数解析为电脑本地绝对路径（默认根为共享目录）。"""
    if not p:
        return root
    return os.path.abspath(p.replace("/", os.sep))


def list_entries(path: str):
    """列出目录内容（目录在前，按名称排序）。"""
    entries = []
    for name in sorted(os.listdir(path)):
        fp = os.path.join(path, name)
        try:
            st = os.stat(fp)
            entries.append({
                "name": name,
                "is_dir": os.path.isdir(fp),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
        except OSError:
            continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


async def files_handler(request):
    """文件浏览：列出目录。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    root = args.share_dir
    path = resolve_fs_path(root, request.query.get("path", ""))
    audit(request.app, "文件浏览", client_ip(request), path)
    try:
        return web.json_response({
            "ok": True, "path": path,
            "parent": os.path.dirname(path),
            "entries": list_entries(path),
        })
    except OSError as e:
        return web.json_response({"ok": False, "error": str(e)})


async def upload_handler(request):
    """上传：手机 → 电脑，原始字节流写盘。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    root = args.share_dir
    os.makedirs(root, exist_ok=True)
    dirpath = resolve_fs_path(root, request.query.get("path", ""))
    name = safe_file_name(request.query.get("name", "upload.bin"))
    dest = os.path.join(dirpath, name)
    tmp = dest + f".{secrets.token_hex(3)}.part"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.content.iter_chunked(256 * 1024):
                f.write(chunk)
                size += len(chunk)
        os.replace(tmp, dest)
        audit(request.app, "文件上传", client_ip(request), f"{name} ({size}B → {dirpath})")
        return web.json_response({"ok": True, "name": name, "size": size, "path": dirpath})
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def download_handler(request):
    """下载：电脑 → 手机，流式发送。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    root = args.share_dir
    path = resolve_fs_path(root, request.query.get("path", ""))
    if not os.path.isfile(path):
        return web.Response(status=404, text="file not found")
    audit(request.app, "文件下载", client_ip(request), path)
    name = os.path.basename(path)
    size = os.path.getsize(path)
    resp = web.StreamResponse(headers={
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Length": str(size),
    })
    await resp.prepare(request)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            await resp.write(chunk)
    return resp


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
    print(f"   免输识别码(一次性) : 用手机相机扫下面的二维码，或打开:", flush=True)
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
                print(f"   3. 中继是否已启动、地址/端口/协议(ws|wss)是否正确", flush=True)
                print(f"   4. 识别码/口令与中继端 --devices 登记的必须一致", flush=True)
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


async def _pace(loop, fps: float, t0):
    """按目标帧率补足剩余时间，采集耗时不计入周期。"""
    remaining = 1.0 / fps - (loop.time() - t0)
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _capture_loop(ws, streamer, monitor, loop):
    """中继上行：屏幕帧推流。"""
    while True:
        t0 = loop.time()
        if monitor.check_gil_pause(streamer):
            await _pace(loop, 1.0, t0)
            continue
        data = await loop.run_in_executor(None, streamer.capture)
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


# ---------------------------------------------------------------- HTTP 处理

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
        while True:
            rgb, w, h = await q.get()
            cx, cy = cursor_frame(streamer)
            msgs = await loop.run_in_executor(None, enc.diff, rgb, w, h, cx, cy)
            for m in msgs:
                await ws.send_bytes(m)
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    finally:
        request.app["frames"].unsubscribe(q)
    return ws


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
    # 让健康监控知道谁在观看（无观看者时才启用"繁忙暂停采集"自保）
    streamer._owner_fs = frames
    streamer._owner_bc = broadcaster
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


def main():
    # 输出跟随系统编码，仅容错不可编码字符（防 GBK 管道/终端因 emoji 崩溃）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="FreeRemote 手机远程控制电脑")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    parser.add_argument("--fps", type=int, default=8, help="推流帧率 1-30（默认 8）")
    parser.add_argument("--preset", default="mid", choices=list(QUALITY_PRESETS),
                        help="默认画质 low/mid/high（默认 mid）")
    parser.add_argument("--monitor", type=int, default=1, help="显示器编号，1=主屏（默认 1）")
    parser.add_argument("--share-dir", default=default_share_dir(),
                        help="文件互传目录（默认自动选非系统盘专用文件夹 D:\\FreeRemoteFiles）")
    parser.add_argument("--token", default="", help="访问口令（默认读 token.txt，无则自动生成永久口令）")
    parser.add_argument("--once", action="store_true",
                        help="一次性口令模式：生成 10 分钟有效的临时口令/链接，过期自动作废（不写盘）")
    parser.add_argument("--renew-token", nargs="?", const="0", default=None,
                        help="更换新口令并写入 token.txt（默认永久有效；--renew-token 30 = 30 天有效）")
    parser.add_argument("--no-auth", action="store_true", help="关闭口令校验（仅限可信局域网）")
    parser.add_argument("--allow-ips", default="",
                        help="IP 白名单，逗号分隔（默认允许所有 IP，需配合口令）")
    parser.add_argument("--tls-cert", default="", help="TLS 证书文件路径（可选，启用 HTTPS）")
    parser.add_argument("--tls-key", default="", help="TLS 私钥文件路径（可选，启用 HTTPS）")
    parser.add_argument("--relay", default="",
                        help="识别码模式：中继服务器地址，如 ws://x.com:9090 或 wss://x.com")
    parser.add_argument("--id", default="", help="识别码模式：设备识别码（默认随机 6 位数字）")
    parser.add_argument("--password", default="", help="识别码模式：设备口令（默认随机生成）")
    parser.add_argument("--cookie", default="",
                        help="识别码模式：连接时携带的会话 Cookie（中继网关如需登录会话时使用，\n"
                             "可在浏览器开发者工具里复制 Cookie 填这里）")
    args = parser.parse_args()

    if args.renew_token is not None:
        # 更换口令并写入 token.txt：默认永久有效；--renew-token 30 = 30 天；0 = 永久
        try:
            days = int(args.renew_token or "0")
        except ValueError:
            days = 0
        new_tok = gen_token()
        expire = int(time.time()) + days * 86400 if days > 0 else None
        save_token_file(new_tok, expire)
        out("=" * 58)
        out(" 已生成新口令（{}）：".format(f"{days} 天有效" if days else "永久有效"))
        out(f"   访问口令 : {new_tok}")
        if expire:
            out(f"   有效期至 : {time.strftime('%Y-%m-%d %H:%M', time.localtime(expire))}")
        out("   ⚠ 旧口令已作废，请用下面的新链接更新手机书签")
        primary, ips = lan_ips()
        out(f"   手机访问 : http://{primary}:{args.port}/?token={new_tok}")
        ts = tailscale_ips()
        if ts:
            out(f"   手机访问(Tailscale跨网) : http://{ts[0]}:{args.port}/?token={new_tok}")
        out("=" * 58)
        sys.exit(0)

    token_expire = None
    if not args.token:
        # 固定口令优先：读 token.txt（新格式 = 永久有效；兼容旧格式，自动归一化）
        args.token, token_expire = load_token_file()
        if args.token:
            if token_expire:
                left = int(token_expire - time.time())
                log("INFO", f"已使用固定口令（token.txt）：{args.token}"
                            f"（有效期至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(token_expire))}，剩 {left // 86400} 天）")
            else:
                log("INFO", f"已使用固定口令（token.txt）：{args.token}（永久有效）")
    if not args.token:
        # 没有口令文件：生成直观口令并保存（永久有效，重启/换网都不变）
        args.token = gen_token()
        save_token_file(args.token, None)
        log("INFO", f"已生成新口令（token.txt，永久有效）：{args.token}")
    args.allow_ips = set(x.strip() for x in args.allow_ips.split(",") if x.strip())

    pyautogui.FAILSAFE = False  # 远程控制时关闭"鼠标移到左上角即中止"的安全熔断

    if args.relay:
        # ---- 识别码模式：跨网络，经中继服务器 ----
        if not args.id:
            args.id = random_id()
        if not args.password:
            args.password = gen_token()
        sys.exit(asyncio.run(relay_client(args)) or 0)
        return

    app = make_app(args)
    app["token_expire"] = token_expire
    if args.once:
        once_tok = gen_token()
        app["once"] = {"tok": once_tok, "exp": time.time() + 600}
    out("=" * 58)
    out(" FreeRemote 已启动 —— 手机浏览器远程控制电脑")
    out(f"   本机预览 : http://127.0.0.1:{args.port}/?token={args.token}")
    primary, ips = lan_ips()
    if len(ips) == 1:
        out(f"   手机访问 : http://{ips[0]}:{args.port}/?token={args.token}")
    else:
        out(f"   手机访问 : http://{primary}:{args.port}/?token={args.token}   ← 最可能")
        for ip in ips[1:]:
            if ip == "127.0.0.1":
                continue
            out(f"       其他  : http://{ip}:{args.port}/?token={args.token}")
        if "127.0.0.1" in ips and len(ips) > 2:
            out(f"       其他  : http://127.0.0.1:{args.port}/?token={args.token}（仅本机可用）")
    ts = tailscale_ips()
    if ts:
        out(f"   手机访问(Tailscale跨网) : http://{ts[0]}:{args.port}/?token={args.token}   ← 不同网络用这个")
    if args.token and not args.no_auth:
        if token_expire:
            left = int(token_expire - time.time())
            out(f"   访问口令 : {args.token}（有效期至 "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(token_expire))}，剩 {left // 86400} 天）")
        else:
            out(f"   访问口令 : {args.token}（永久有效，保存在 token.txt）")
        out("   变更口令 : 双击 renew-token.bat（换新口令后手机书签同步更新）")
    if args.once:
        out("  ────────────────────────────────────────────────")
        out("   一次性口令（10 分钟有效，过期自动作废，不写盘）：")
        out(f"      {once_tok}")
        out(f"   一次性链接 : http://{primary}:{args.port}/?token={once_tok}")
        if ts:
            out(f"      (跨网) : http://{ts[0]}:{args.port}/?token={once_tok}")
        out("  ────────────────────────────────────────────────")
    if IS_WIN:
        out("   提示     : 首次运行若弹防火墙提示请点「允许访问」")
    out(f"   文件互传目录 : {args.share_dir}")
    if args.no_auth:
        out("   ⚠ 已关闭口令校验，请勿暴露到公网")
    if args.allow_ips:
        out(f"   IP白名单 : {', '.join(sorted(args.allow_ips))}（其余 IP 一律拒绝）")
    if args.tls_cert and args.tls_key:
        out("   TLS      : 已启用 HTTPS（手机访问请用 https://）")
    out(f"   日志     : {LOG_FILE.relative_to(BASE_DIR)}（UTF-8，错误行含 [ERROR]，可 grep）")
    out("   停止     : 按 Ctrl+C")
    out("=" * 58)
    ssl_ctx = None
    if args.tls_cert and args.tls_key:
        import ssl
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(args.tls_cert, args.tls_key)
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
