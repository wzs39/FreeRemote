# -*- coding: utf-8 -*-
"""采集与编码：屏幕推流核心（MJPEG 备胎 + 分块增量主路径）。"""
import asyncio
import io
import struct
import zlib

import mss
import numpy as np
from PIL import Image

from .command import _INJ  # 统一注入调度（command 不依赖 capture，无环）
from .config import QUALITY_PRESETS
from .health import HealthMonitor
from .logging_util import log

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

    def probe_capture(self) -> bool:
        """轻量采集探针：不产生推流帧，只验证 BitBlt/GDI 链路是否恢复。

        锁屏/安全桌面期间 grab 抛错，解锁后立即恢复正常——用探针探测恢复，
        避免"每帧都抛错刷日志"或"恢复后还要等重建"的窗口。
        """
        try:
            img = self.sct.grab({"left": 0, "top": 0, "width": 32, "height": 32})
            return bool(img and img.size)
        except Exception:
            return False


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
                if data and data != self.streamer._fallback:
                    if self.streamer.health:
                        self.streamer.health.maybe_heal(self.streamer)  # 自愈
                    for q in list(self.subs):
                        try:
                            q.put_nowait(data)
                        except asyncio.QueueFull:
                            pass  # 慢客户端丢帧
                elif self.streamer.health:
                    self.streamer.health.note_fail(Exception("capture returned fallback"))
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
                    # 主推流路径同样自愈：BitBlt 连续失败时重建采集器（此前仅 MJPEG/中继路径有）
                    if self.streamer.health:
                        self.streamer.health.maybe_heal(self.streamer)
                    for q in list(self.subs):
                        try:
                            q.put_nowait((rgb, w, h))
                        except asyncio.QueueFull:
                            pass
                else:
                    # capture_rgb 返回空 = 采集失败：立即尝试自愈，不等 5 帧
                    if self.streamer.health:
                        self.streamer.health.maybe_heal(self.streamer)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log("ERROR", f"分块采集错误：{exc}")
                if self.streamer.health:
                    self.streamer.health.note_fail(exc)
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
        self._prev_flat = b""  # 上一帧原始字节（向量化 diff 用）

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
        self._prev_flat = rgb

    def _as_grid(self, rgb, w, h):
        """原始 RGB 字节 → (rows, cols, 64, 64, 3) uint8 网格（向量化 diff 用）。"""
        cols, rows = self.grid(w, h)
        arr = np.frombuffer(rgb, dtype=np.uint8)
        if w % self.BLOCK or h % self.BLOCK:  # 边缘不齐：先补齐到整块
            pad_h = rows * self.BLOCK - h
            pad_w = cols * self.BLOCK - w
            arr = np.pad(arr.reshape(h, w, 3),
                         ((0, pad_h), (0, pad_w), (0, 0)))
            h, w = rows * self.BLOCK, cols * self.BLOCK
        return arr.reshape(rows, self.BLOCK, cols, self.BLOCK, 3).swapaxes(1, 2)

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
        # 向量化 diff：整帧一次 C 速度对比，替代逐块 Python 循环
        grid = self._as_grid(rgb, w, h)
        prev_grid = self._as_grid(self._prev_flat, w, h)
        changed_mask = np.any(grid != prev_grid, axis=(2, 3, 4))
        changed_idx = np.flatnonzero(changed_mask.ravel())
        changed = changed_idx.tolist()  # 后续 CRC/JPEG/回填流程沿用原列表逻辑
        prev = self.prev
        for idx in changed:
            prev[idx] = self.block_bytes(rgb, w, h, idx // cols, idx % cols)
        self._prev_flat = rgb
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


def cursor_frame(streamer):
    """当前鼠标位置 → 帧坐标（用于客户端本地绘制光标）。"""
    try:
        x, y = _INJ.position()
    except Exception:
        return 0, 0
    m = streamer.monitor
    return int((x - m["left"]) * streamer.scale), int((y - m["top"]) * streamer.scale)


async def _pace(loop, fps: float, t0):
    """按目标帧率补足剩余时间，采集耗时不计入周期。"""
    remaining = 1.0 / fps - (loop.time() - t0)
    if remaining > 0:
        await asyncio.sleep(remaining)

