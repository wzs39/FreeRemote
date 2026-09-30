# -*- coding: utf-8 -*-
"""健康监控策略锁：以 maybe_heal（唯一策略入口）的真实行为为准。

用桩 streamer 记录 reinit/probe/set_preset 调用，锁定：
  连续 2 次失败触发重建、3 秒防抖、探针通过清零、探针失败静默重试；
  以及自动降档后的画质自动回升（稳定 10 秒升回一档，手动切档即取消）。
"""
from free_remote.health import HealthMonitor


class FakeStreamer:
    preset = "mid"
    probe_ok = True

    def __init__(self):
        self.reinits = 0
        self.probes = 0
        self.preset_changes = []

    def reinit(self):
        self.reinits += 1

    def probe_capture(self):
        self.probes += 1
        return self.probe_ok

    def set_preset(self, p):
        self.preset = p
        self.preset_changes.append(p)


def make(fps=8.0):
    return HealthMonitor(fps)


def test_two_consecutive_failures_trigger_one_reinit():
    m, s = make(), FakeStreamer()
    m.note_fail("e1")
    m.maybe_heal(s)
    assert s.reinits == 0  # 1 次失败不触发
    m.note_fail("e2")
    m.maybe_heal(s)
    assert s.reinits == 1  # 连续 2 次 → 重建
    assert s.probes == 1


def test_probe_success_resets_counter():
    m, s = make(), FakeStreamer()
    m.note_fail("e1"); m.note_fail("e2")
    m.maybe_heal(s)
    assert m.consecutive_fail == 0  # 探针通过 → 计数清零


def test_probe_failure_retries_silently():
    m, s = make(), FakeStreamer()
    s.probe_ok = False
    m.note_fail("e1"); m.note_fail("e2")
    m.maybe_heal(s)
    assert m.consecutive_fail == 1  # 锁屏期：留 1 次计数继续重试


def test_reinit_debounce_3s(monkeypatch):
    m, s = make(), FakeStreamer()
    t = [1000.0]
    monkeypatch.setattr("free_remote.health.time.monotonic", lambda: t[0])
    m.note_fail("e1"); m.note_fail("e2")
    m.maybe_heal(s)
    assert s.reinits == 1
    m.note_fail("e3"); m.note_fail("e4")
    m.maybe_heal(s)
    assert s.reinits == 1  # 防抖期内不重建
    t[0] += 3.5
    m.note_fail("e5"); m.note_fail("e6")
    m.maybe_heal(s)
    assert s.reinits == 2  # 防抖期过 → 再次重建


def test_note_ok_resets():
    m = make()
    m.note_fail("e"); m.note_fail("e")
    m.note_ok()
    s = FakeStreamer()
    m.maybe_heal(s)
    assert s.reinits == 0
    assert m.consecutive_fail == 0


# ---- 画质自动回升：稳定 10 秒升回一档，终点=降档前档位 ----

def _clock(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr("free_remote.health.time.monotonic", lambda: t[0])
    return t


def _downgrade(m, s, from_preset="high"):
    """制造一次自动降档：帧率跌到阈值之下 → 单步降一档。"""
    s.preset = from_preset
    m.fps_ema = 2.0  # < target 8 * 0.5
    m.maybe_heal(s)


def test_auto_upgrade_after_stable_fps(monkeypatch):
    m, s, t = make(), FakeStreamer(), _clock(monkeypatch)
    _downgrade(m, s, "high")
    assert s.preset == "mid" and m.degraded
    m.fps_ema = 7.5  # ≥ 8*0.85 稳定线
    m.maybe_heal(s)
    assert s.preset == "mid"  # 稳定窗第一拍：只起表，不动档
    t[0] += 10.5
    m.maybe_heal(s)
    assert s.preset == "high"  # 持续稳定 10 秒 → 升回降档前档位
    assert not m.degraded


def test_upgrade_window_resets_on_fps_drop(monkeypatch):
    m, s, t = make(), FakeStreamer(), _clock(monkeypatch)
    _downgrade(m, s, "high")
    m.fps_ema = 7.5
    m.maybe_heal(s)  # 起表
    t[0] += 5
    m.fps_ema = 2.0
    m.maybe_heal(s)  # 跌回阈值之下 → 计时清零
    t[0] += 5
    m.fps_ema = 7.5
    m.maybe_heal(s)  # 重新起表
    t[0] += 9.5
    m.maybe_heal(s)
    assert s.preset == "mid"  # 新窗口未满 10 秒，不升
    t[0] += 1.0
    m.maybe_heal(s)
    assert s.preset == "high"


def test_upgrade_stops_at_original_preset(monkeypatch):
    """mid→low 降档只回升到 mid，绝不自行升到 high。"""
    m, s, t = make(), FakeStreamer(), _clock(monkeypatch)
    _downgrade(m, s, "mid")
    assert s.preset == "low"
    m.fps_ema = 7.5
    m.maybe_heal(s)
    t[0] += 10.5
    m.maybe_heal(s)
    assert s.preset == "mid"
    assert not m.degraded
    t[0] += 30  # 不再继续升档
    m.maybe_heal(s)
    assert s.preset == "mid"


def test_manual_preset_cancels_upgrade(monkeypatch):
    m, s, t = make(), FakeStreamer(), _clock(monkeypatch)
    _downgrade(m, s, "high")
    m.fps_ema = 7.5
    m.maybe_heal(s)  # 起表
    m.on_manual_preset("mid")  # 用户手动切档 → 取消自动回升
    assert not m.degraded
    t[0] += 11
    m.maybe_heal(s)
    assert s.preset == "mid"  # 不再自动升


def test_manual_return_to_original_clears_state(monkeypatch):
    """兑底：用户手动切回原档位（未经 on_manual_preset 通知），下次自检清残留状态。"""
    m, s = make(), FakeStreamer()
    _downgrade(m, s, "high")
    s.set_preset("high")  # 直接切回，模拟监控未收到通知的路径
    m.fps_ema = 7.5
    m.maybe_heal(s)
    assert s.preset == "high"  # 不重复 set_preset
    assert not m.degraded
