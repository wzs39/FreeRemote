# -*- coding: utf-8 -*-
"""DXGI 抓屏后端行为锁：静止哨兵、双后端回退、缩放契约、健康语义。

不依赖真实 DXGI：dxcam 以假模块注入 sys.modules，输出顺序与
FrameSource._run 的分发逻辑逐一对齐。
"""
import asyncio
import sys
import types

import numpy as np
import pytest

from free_remote import dxgi as dxgi_mod
from free_remote.capture import ERROR, STILL, FrameSource, Streamer
from free_remote.health import HealthMonitor


class FakeMSS:
    """Streamer 构造需要 mss；monitor 用 dict（与真实 mss 一致，可下标取值）。"""

    monitors = [
        {"left": 0, "top": 0, "width": 640, "height": 400},   # 全虚拟屏
        {"left": 0, "top": 0, "width": 320, "height": 200},   # 主显示器
        {"left": 320, "top": 0, "width": 320, "height": 200},  # 副显示器
    ]


class FakeCam:
    """可编程的假 camera：队列决定 grab() 返回序列。"""

    def __init__(self, script):
        self.script = list(script)
        self.grabs = 0
        self.released = False

    def grab(self):
        self.grabs += 1
        if not self.script:
            return None
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item is None:
            return None
        return item  # np.ndarray (h, w, 3)

    def release(self):
        self.released = True


def install_fake_dxcam(monkeypatch, cam):
    """以假 dxcam 模块注入 sys.modules 并重置探测缓存。"""
    fake = types.ModuleType("dxcam")
    fake.create = lambda **kwargs: cam
    monkeypatch.setitem(sys.modules, "dxcam", fake)
    monkeypatch.setattr(dxgi_mod, "_probe_result", None)
    return fake


def frame_px(w, h, value):
    return np.full((h, w, 3), value, dtype=np.uint8)


@pytest.fixture
def env(monkeypatch):
    """DXGI 可用 + 假 mss 的公共环境，返回 (Streamer, 可编程 cam)。"""
    monkeypatch.delenv("FREE_REMOTE_CAPTURE_BACKEND", raising=False)
    cam = FakeCam([frame_px(320, 200, 1)])
    install_fake_dxcam(monkeypatch, cam)
    monkeypatch.setattr("free_remote.capture.mss.MSS", FakeMSS)
    s = Streamer(1, "mid", 8, HealthMonitor(8))
    return s, cam


def test_backend_selected_for_primary_monitor(env):
    s, _ = env
    assert s.dxgi is not None  # 主显示器 + dxcam 可导入 → DXGI 后端


def test_mss_backend_on_secondary_monitor(monkeypatch):
    cam = FakeCam([])
    install_fake_dxcam(monkeypatch, cam)
    monkeypatch.setattr("free_remote.capture.mss.MSS", FakeMSS)
    s = Streamer(2, "mid", 8, HealthMonitor(8))
    assert s.dxgi is None  # v1 仅主显示器；其余回退 mss


def test_still_sentinel_passthrough(env):
    """grab 返回 None → capture_rgb 返回 STILL 哨兵（is 同一性）。"""
    s, cam = env
    cam.script = [None]
    got = s.capture_rgb()
    assert got is STILL


def test_error_sentinel_on_exception(env):
    s, cam = env
    cam.script = [RuntimeError("device lost")]
    got = s.capture_rgb()
    assert got is ERROR


def test_frame_scaled_to_frame_size(env):
    """DXGI 原始帧必须缩放到 frame_size（与 mss 路径同契约）。"""
    s, cam = env
    cam.script = [frame_px(640, 400, 7)]  # 与 frame_size(320,200) 不同
    rgb, w, h = s.capture_rgb()
    assert (w, h) == s.frame_size
    assert len(rgb) == w * h * 3
    assert s.health.capture_ok == 1


def test_still_resets_fail_counter_but_not_fps(env):
    """静止刷新 last_frame_t/清零连续失败，但不计入帧率样本。"""
    s, cam = env
    s.health.note_fail("x")
    s.health.note_fail("x")
    cam.script = [None]
    assert s.capture_rgb() is STILL
    assert s.health.consecutive_fail == 0
    ema_before = s.health.fps_ema
    cam.script = [None]
    s.capture_rgb()
    assert s.health.fps_ema == ema_before  # 静止不影响 fps_ema


def test_probe_env_override_mss(monkeypatch):
    """环境变量强制 mss：即使 dxcam 可导入也不用 DXGI。"""
    monkeypatch.setenv("FREE_REMOTE_CAPTURE_BACKEND", "mss")
    monkeypatch.setattr(dxgi_mod, "_probe_result", None)
    assert dxgi_mod.is_available() is False


def test_probe_env_override_dxgi(monkeypatch):
    monkeypatch.setenv("FREE_REMOTE_CAPTURE_BACKEND", "dxgi")
    monkeypatch.setattr(dxgi_mod, "_probe_result", None)
    assert dxgi_mod.is_available() is True


def test_probe_cached(monkeypatch):
    monkeypatch.setattr(dxgi_mod, "_probe_result", None)
    assert dxgi_mod.is_available() is dxgi_mod.is_available()
    assert dxgi_mod._probe_result is not None  # 已缓存


def test_close_releases_camera(env):
    s, cam = env
    s.close_backends()
    assert cam.released


def test_reinit_reopens_dxgi(env):
    s, cam = env
    s.reinit()
    assert s.dxgi is not None  # 重建后仍是 DXGI 后端
    assert cam.released  # 旧 camera 已释放


def test_framesource_dispatches_still_and_error(env):
    """FrameSource._run 的分发：静止/失败不入队，正常帧入队。"""
    s, cam = env
    cam.script = [None, RuntimeError("x"), frame_px(320, 200, 3), None]
    fs = FrameSource(s)
    s.register_view_source(fs)

    async def drive():
        s.health.maybe_heal = lambda st: None  # 隔离自动降档（真实时钟下 EMA 会抖动）
        q = fs.subscribe()
        await asyncio.sleep(0.35)  # 8fps → 覆盖 4 次循环
        fs.unsubscribe(q)
        return q

    q = asyncio.run(asyncio.wait_for(drive(), timeout=3))
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert len(items) == 1  # 只有第 3 次的真帧入队
    rgb, w, h = items[0]
    assert (w, h) == s.frame_size


def test_framesource_health_on_error(env):
    """ERROR 帧触发 maybe_heal（不自增 note_fail，capture_rgb 内已计）。"""
    s, cam = env
    fails_before = s.health.capture_fail
    cam.script = [RuntimeError("boom")]
    got = s.capture_rgb()
    assert got is ERROR
    assert s.health.capture_fail == fails_before + 1
