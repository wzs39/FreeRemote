# -*- coding: utf-8 -*-
"""DXGI Desktop Duplication 抓屏后端（Windows）。

与 mss/BitBlt「每帧全屏轮询」不同，DXGI 由系统合成器推送新帧：
画面静止时 AcquireNextFrame 立即超时，不产生任何像素拷贝——
静止场景（看文档/挂机）服务端采集 CPU 接近零，动态画面时由 GPU
直接给出像素（免 BitBlt 软件拷贝），速度通常快 3~5 倍。

capture_rgb 返回三元状态（供上层 FrameSource 分发）：
  ("frame", rgb_bytes, w, h)  有新帧
  STILL ("still", b"", 0, 0)  无新帧（自上一帧以来画面无变化）
  ERROR ("error", b"", 0, 0)  本帧失败（连续失败由上层 HealthMonitor 自愈）

约束：v1 仅支持主显示器（mss monitor_idx==1），其余显示器由上层自动
回退 mss 路径，行为与旧版一致。dxcam 不可用（驱动/会话不支持）时
is_available() 为 False，全部流量走 mss。
"""
import atexit
import os
import time

import numpy as np
from PIL import Image
from .logging_util import log

# 静止哨兵：上层用「is」比较同一对象，避免内容比较
STILL = ("still", b"", 0, 0)
ERROR = ("error", b"", 0, 0)

# 进程内探测缓存：重复创建/释放 DXGI 会话代价高且易踩坑，只探测一次
_probe_result: bool | None = None


def is_available() -> bool:
    """探测当前会话能否使用 DXGI Desktop Duplication（结果缓存）。

    环境变量 FREE_REMOTE_CAPTURE_BACKEND=mss 可强制关闭（排障/测试用）；
    =dxgi 强制启用探测；默认 auto 按导入结果决定。
    """
    global _probe_result
    if _probe_result is not None:
        return _probe_result
    mode = os.environ.get("FREE_REMOTE_CAPTURE_BACKEND", "auto").strip().lower()
    if mode == "mss":
        _probe_result = False
        return False
    try:
        import dxcam  # 仅 Windows 可导入
        _probe_result = dxcam is not None
    except Exception as exc:  # ImportError / COM 初始化失败等
        log("INFO", f"DXGI 后端不可用，使用 mss 回退：{exc}")
        _probe_result = False
    return _probe_result


class DxgiCapturer:
    """dxcam 封装：主输出采集、无新帧检测、连续失败自动重建。"""

    def __init__(self):
        import dxcam
        self._dxcam = dxcam
        self._cam = None
        self._fails = 0
        self._last_err_log = 0.0  # 同类异常限频：首记 + 每 30 秒提醒一次
        self._open()
        atexit.register(self.close)  # 进程退出时释放 DXGI 会话（Ctrl+C/看门狗同理）

    def _open(self):
        """创建 camera。output_idx=None 选主输出；RGB 直出（dxcam 的 C 内核
        做 BGRA→RGB，实测比自写 numpy 换道快 ~20%）；numpy 处理内核免 cv2。"""
        self._cam = self._dxcam.create(output_idx=None, output_color="RGB",
                                       processor_backend="numpy")

    def reopen(self):
        """整体重建（输出丢失/采集中断的兜底，与 Streamer.reinit 配套）。"""
        self.close()
        self._open()

    def close(self):
        cam, self._cam = self._cam, None
        if cam is not None:
            try:
                cam.release()
            except Exception:
                pass

    def capture_rgb(self, tw=None, th=None):
        """抓一帧并输出 RGB 字节。线程池中调用；绝不抛异常。

        tw/th 为目标帧尺寸（Streamer.frame_size）：给出且与原生不同时在
        本函数内完成缩放（BILINEAR，与 mss 路径同参数）。
        """
        try:
            cam = self._cam
            if cam is None:
                return ERROR
            # new_frame_only=True（默认）：无新帧立即返回 None，AcquireNextFrame
            # 以 0 超时非阻塞探测——静止时开销仅为一次系统调用
            frame = cam.grab()
            if frame is None:
                return STILL
            h, w = frame.shape[:2]
            if tw and th and (tw, th) != (w, h):
                pil = Image.frombytes("RGB", (w, h), frame.tobytes())
                pil = pil.resize((tw, th), Image.BILINEAR)
                frame = np.asarray(pil)
                w, h = tw, th
            return "frame", frame.tobytes(), w, h
        except Exception as exc:
            now = time.monotonic()
            if now - self._last_err_log > 30:
                self._last_err_log = now
                log("ERROR", f"DXGI 采集异常（连续第 {self._fails + 1} 次）：{type(exc).__name__}: {exc}")
            self._fails += 1
            if self._fails >= 8:
                # dxcam 自带输出丢失恢复；连续失败时整体重建兜底
                self._fails = 0
                try:
                    self.reopen()
                except Exception as exc2:
                    log("WARN", f"DXGI 重建失败：{exc2}")
            return ERROR
