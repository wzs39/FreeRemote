# -*- coding: utf-8 -*-
"""click 指令边界对抗测试：count 钳制、未知按钮归一化、修饰键回滚。

背景（真实事故）：手机左键点击曾因 SendInput 标志错位变成"右键按下永不抬起"。
本文件锁住调度层把守的边界：恶意/畸形 count 与 button 不得直通注入引擎，
修饰键按下中途失败必须回滚已按下的键。
"""
import pytest

from free_remote import command
from free_remote.command import apply_command
from tests.test_command import InjSpy, S


@pytest.fixture
def spy(monkeypatch):
    s = InjSpy()
    monkeypatch.setattr(command, "_INJ", s)
    return s


# ---- count 边界：任意值不得直通 SendInput ----

@pytest.mark.parametrize("raw,expect", [
    (0, 1), (-7, 1), (2, 2), (3, 3), (999, 3),
    ("2", 2), ("abc", 1), (None, 1), (2.9, 2), ([], 1),
])
def test_count_clamped(spy, raw, expect):
    cmd = {"t": "click", "x": 10, "y": 20}
    if raw is not None:
        cmd["count"] = raw
    apply_command(S(), cmd)
    # S.scale=0.5：frame(10,20) → screen(20,40)
    assert spy.calls == [("click", 20.0, 40.0, "left", expect)]


# ---- button：未知值显式归一化为 left，不得静默漂移 ----

@pytest.mark.parametrize("btn", ["x1", "", "LEFT", None, "side"])
def test_unknown_button_normalized(spy, btn):
    cmd = {"t": "click", "x": 1, "y": 2}
    if btn is not None:
        cmd["button"] = btn
    apply_command(S(), cmd)
    assert spy.calls[0][3] == "left"


@pytest.mark.parametrize("btn", ["left", "right", "middle"])
def test_known_button_pass_through(spy, btn):
    apply_command(S(), {"t": "click", "x": 1, "y": 2, "button": btn})
    assert spy.calls[0][3] == btn


# ---- 修饰键回滚：keyDown 中途抛出时，已按下的键必须被释放 ----

class PartialFailInj(InjSpy):
    """第二个修饰键 keyDown 抛出（模拟未知键名）。"""

    def keyDown(self, k):
        self.calls.append(("keyDown", k))
        if k == "ghost":
            raise ValueError(f"未知键名：{k}")


def test_mods_rollback_on_failure(monkeypatch):
    inj = PartialFailInj()
    monkeypatch.setattr(command, "_INJ", inj)
    apply_command(S(), {"t": "click", "x": 1, "y": 2,
                        "mods": ["ctrl", "ghost", "shift"]})
    downs = [c[1] for c in inj.calls if c[0] == "keyDown"]
    ups = [c[1] for c in inj.calls if c[0] == "keyUp"]
    assert downs == ["ctrl", "ghost"], "shift 不应在 ghost 失败后被按下"
    assert ups == ["ctrl"], "已按下的 ctrl 必须被回滚释放"


def test_mods_released_on_injection_error(monkeypatch):
    """click 注入本身抛出时修饰键同样回滚（fn 在 try 内）。"""
    class BoomInj(InjSpy):
        def click(self, *a, **kw):
            raise RuntimeError("注入失败")

    inj = BoomInj()
    monkeypatch.setattr(command, "_INJ", inj)
    apply_command(S(), {"t": "click", "x": 1, "y": 2, "mods": ["ctrl"]})
    ups = [c[1] for c in inj.calls if c[0] == "keyUp"]
    assert ups == ["ctrl"]
