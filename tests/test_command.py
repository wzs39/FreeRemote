# -*- coding: utf-8 -*-
"""指令调度锁：apply_command 的路由、坐标换算与修饰键状态所有权。"""
import pytest

from free_remote import command
from free_remote.command import apply_command


class InjSpy:
    """记录注入调用的假引擎（monkeypatch 掉 command._INJ）。"""

    def __init__(self):
        self.calls = []

    def moveTo(self, x, y):
        self.calls.append(("moveTo", x, y))

    def click(self, x, y, button="left", clicks=1):
        self.calls.append(("click", x, y, button, clicks))

    def scroll(self, dy=0, dx=0):
        self.calls.append(("scroll", dy, dx))

    def hscroll(self, dx):
        self.calls.append(("hscroll", dx))

    def keyDown(self, k):
        self.calls.append(("keyDown", k))

    def keyUp(self, k):
        self.calls.append(("keyUp", k))

    def press(self, k):
        self.calls.append(("press", k))

    def typewrite(self, text, interval=0.0):
        self.calls.append(("typewrite", text))

    def hotkey(self, *keys):
        self.calls.append(("hotkey",) + keys)


@pytest.fixture
def spy(monkeypatch):
    s = InjSpy()
    monkeypatch.setattr(command, "_INJ", s)
    return s


class S:
    """坐标换算桩：scale=0.5。"""
    scale = 0.5
    monitor = {"left": 0, "top": 0}

    def frame_to_screen(self, x, y):
        return x / self.scale, y / self.scale

    def set_preset(self, p):
        self.preset = p

    def set_resolution(self, v):
        self.res = v


def test_move_uses_frame_to_screen(spy):
    cmd = S()
    apply_command(cmd, {"t": "move", "x": 100, "y": 60})
    assert spy.calls == [("moveTo", 200.0, 120.0)]


def test_click_with_mods_wraps(spy):
    c = S()
    apply_command(c, {"t": "click", "x": 10, "y": 10, "mods": ["ctrl"], "button": "right"})
    assert spy.calls[0] == ("keyDown", "ctrl")
    assert spy.calls[1][0] == "click"
    assert spy.calls[-1] == ("keyUp", "ctrl")


def test_releasekeys_routes_to_force_release(spy, monkeypatch):
    seen = []
    monkeypatch.setattr(command, "force_release_all_mods", lambda r="": seen.append(r))
    apply_command(S(), {"t": "releasekeys"})
    assert seen and "手机端请求" in seen[0]


def test_key_combo_order(spy):
    apply_command(S(), {"t": "combo", "keys": ["ctrl", "alt", "del"]})
    kinds = [(c[0], c[1] if len(c) > 1 else None) for c in spy.calls]
    assert kinds == [("keyDown", "ctrl"), ("keyDown", "alt"),
                     ("press", "del"), ("keyUp", "alt"), ("keyUp", "ctrl")]


def test_text_ascii_goes_to_typewrite(spy):
    apply_command(S(), {"t": "text", "text": "hello"})
    assert ("typewrite", "hello") in spy.calls


def test_text_chinese_uses_clipboard_paste(spy, monkeypatch):
    seen = {}
    monkeypatch.setattr(command.pyperclip, "copy", lambda t: seen.setdefault("t", t))
    apply_command(S(), {"t": "text", "text": "你好"})
    assert seen["t"] == "你好"
    assert spy.calls[-1] == ("hotkey", "ctrl", "v")


def test_unknown_command_does_not_raise(spy):
    apply_command(S(), {"t": "does_not_exist", "junk": 1})  # 只记 WARN 不抛


def test_setpreset_and_setres(spy):
    c = S()
    apply_command(c, {"t": "setpreset", "preset": "high"})
    assert c.preset == "high"
    apply_command(c, {"t": "setres", "scale": 0.8})
    assert c.res == 0.8
    apply_command(c, {"t": "setres", "scale": "auto"})
    assert c.res is None


# ---------------- win_input 修饰键所有权 ----------------

def test_win_input_mod_state_ownership():
    """有 win_input 时（Windows CI/本机）：_held 是唯一事实来源。"""
    win_input = pytest.importorskip("free_remote.win_input")
    if not getattr(win_input, "available", False):
        pytest.skip("非 Windows")
    assert not win_input.is_held("ctrl")
    assert win_input.mod_down("ctrl") is True
    assert win_input.is_held("ctrl")
    assert win_input.release_all() == 1
    assert not win_input.is_held("ctrl")
    assert win_input.release_all() == 0  # 幂等，不盲发
