# -*- coding: utf-8 -*-
"""健康监控策略锁：以 maybe_heal（唯一策略入口）的真实行为为准。

用桩 streamer 记录 reinit/probe/set_preset 调用，锁定：
  连续 2 次失败触发重建、3 秒防抖、探针通过清零、探针失败静默重试。
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
