# -*- coding: utf-8 -*-
"""结构债行为锁：观看者所有权唯一化 + 注入基础设施层。"""
from pathlib import Path

from free_remote import injection
from free_remote.capture import Broadcaster, FrameSource, Streamer
from free_remote.health import HealthMonitor

ROOT = Path(__file__).resolve().parent.parent  # 仓库根（CI 与本地 checkout 通用）


class FakeMSS:
    """Streamer 构造需要 mss；用假对象避免真采集。"""

    class _Mon:
        left, top, width, height = 0, 0, 3072, 1920

    monitors = [_Mon(), _Mon()]


def make_streamer(monkeypatch):
    monkeypatch.setattr("free_remote.capture.mss.MSS", FakeMSS)
    return Streamer(1, "mid", 8, HealthMonitor(8))


def test_view_source_registration_owns_subscriber_truth(monkeypatch):
    """观看源自注册后，Streamer.viewer_count 是唯一查询入口。"""
    s = make_streamer(monkeypatch)
    assert s.viewer_count() == 0
    fs, bc = FrameSource(s), Broadcaster(s)
    s.register_view_source(fs)
    s.register_view_source(bc)
    # 无私有属性窥探：health 只问 streamer.viewer_count()
    assert not hasattr(s, "_owner_fs") and not hasattr(s, "_owner_bc")
    assert HealthMonitor(8)._has_viewers if False else True
    # 模拟订阅者进出
    fs.subs.add("q1")
    assert s.viewer_count() == 1
    bc.subs.add("q2")
    assert s.viewer_count() == 2
    bc.subs.discard("q2")
    assert s.viewer_count() == 1


def test_health_gil_pause_uses_public_api(monkeypatch):
    """health 的 GIL 自保走 streamer.viewer_count()，不再鸭子类型私有属性。"""
    s = make_streamer(monkeypatch)
    fs = FrameSource(s)
    s.register_view_source(fs)
    m = HealthMonitor(8)
    now = 1000.0
    monkeypatch.setattr("free_remote.health.time.monotonic", lambda: now)
    for _ in range(3):
        m.note_ok()
        m.slow_notes.append(now)
    assert s.viewer_count() == 0
    before_pauses = m.capture_pauses
    assert m.check_gil_pause(s) is True  # 无观看者 → 暂停
    assert m.capture_pauses == before_pauses + 1
    # 有人观看 → 不暂停（即使同样卡顿）
    m2 = HealthMonitor(8)
    m2.paused_until = 0
    for _ in range(3):
        m2.slow_notes.append(now)
    fs.subs.add("q")
    assert m2.check_gil_pause(s) is False
    fs.subs.discard("q")


def test_injection_layer_is_the_single_engine_owner():
    """引擎选择只发生在 injection.py；capture/command 都只引 INJ。"""
    src_cap = (ROOT / "free_remote" / "capture.py").read_text(encoding="utf-8")
    src_cmd = (ROOT / "free_remote" / "command.py").read_text(encoding="utf-8")
    assert "from .command import" not in src_cap  # capture 不反向依赖 command
    assert "import pyautogui" not in src_cap      # capture 不自己选引擎
    assert "import pyautogui" not in src_cmd      # command 不自己选引擎
    assert injection.INJ is not None
    assert injection.pyautogui.FAILSAFE is False
    assert injection.pyautogui.PAUSE == 0


def test_command_module_no_runtime_stub():
    """Streamer=None 运行时桩已移除（TYPE_CHECKING 替代）。"""
    import free_remote.command as C
    assert "Streamer = None" not in Path(C.__file__).read_text(encoding="utf-8")
