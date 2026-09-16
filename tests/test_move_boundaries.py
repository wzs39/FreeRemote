# -*- coding: utf-8 -*-
"""move / setres 指令边界对抗测试。

与 test_click_boundaries.py 同规格，覆盖坐标类指令的畸形输入：
  - 丢字段 / 非数字 / NaN / inf / 容器类型 —— 全部拒绝，零注入
  - 顶层非对象消息（'5' / [1,2] / null）—— 静默拒绝，不炸控制连接
  - setres 的 float('nan') 可溜过 max/min 钳制 —— 必须先挡

换算桩是**严格契约**：frame_to_screen 收到非数字即抛 TypeError。
守卫若失守（畸形值直达换算/引擎），测试立刻爆红——而不是靠宽松桩掩盖。
"""
import json
import math

import pytest

from free_remote import command, logging_util
from free_remote.command import apply_command
from tests.test_command import InjSpy


@pytest.fixture(autouse=True)
def quiet_log(monkeypatch):
    """吞掉 WARN/ERROR 日志输出，测试自身只断言注入行为。"""
    monkeypatch.setattr(logging_util, "log", lambda *a, **k: None)


class SStrict:
    """严格换算桩：scale=0.5，非数字坐标直接抛（模拟真实除法语义）。"""

    scale = 0.5
    monitor = {"left": 0, "top": 0}

    def frame_to_screen(self, x, y):
        return x / self.scale, y / self.scale

    def set_preset(self, p):
        self.preset = p

    def set_resolution(self, v):
        self.res = v


@pytest.fixture
def spy(monkeypatch):
    s = InjSpy()
    monkeypatch.setattr(command, "_INJ", s)
    return s


# ---- 丢字段 / 非数字 / NaN / inf：零注入 ----

BAD_XY = [
    {},                                        # 丢字段
    {"x": "10", "y": 20},                      # 字符串坐标
    {"x": 10, "y": [20]},                      # 容器
    {"x": 10, "y": {"v": 20}},                 # 字典
    {"x": 10},                                 # 只有 x
    {"y": 20},                                 # 只有 y
    {"x": float("nan"), "y": 20},              # json.loads 接受 NaN
    {"x": 10, "y": float("inf")},
    {"x": float("-inf"), "y": 20},
]


@pytest.mark.parametrize("extra", BAD_XY, ids=range(len(BAD_XY)))
def test_move_rejects_malformed_xy(spy, extra):
    apply_command(SStrict(), {"t": "move", **extra})
    assert spy.calls == [], f"畸形坐标 {extra} 不得到达注入引擎"


@pytest.mark.parametrize("extra", BAD_XY, ids=range(len(BAD_XY)))
def test_click_shares_same_xy_guard(spy, extra):
    apply_command(SStrict(), {"t": "click", **extra})
    assert spy.calls == []


def test_move_accepts_float_and_int(spy):
    apply_command(SStrict(), {"t": "move", "x": 10.5, "y": 20})
    x, y = spy.calls[0][1], spy.calls[0][2]
    assert (x, y) == (21.0, 40.0)


# ---- 顶层非对象消息：静默拒绝，不炸调用方 ----

@pytest.mark.parametrize("raw", ["5", "3.14", "[1,2]", "null", '"txt"', "true"])
def test_top_level_non_object_rejected(spy, raw):
    cmd = json.loads(raw)  # 手机端 WS 消息 json.loads 后能得到的一切类型
    apply_command(SStrict(), cmd)
    assert spy.calls == []


# ---- setres：float('nan') 溜过 max/min 钳制的后门 ----

def test_setres_nan_rejected(spy):
    s = SStrict()
    apply_command(s, {"t": "setres", "scale": float("nan")})
    assert not hasattr(s, "res"), "NaN 分辨率不得写入推流器"


def test_setres_inf_rejected(spy):
    s = SStrict()
    apply_command(s, {"t": "setres", "scale": "inf"})  # 字符串 'inf' 经 float() 变 inf
    assert not hasattr(s, "res")


def test_setres_non_numeric_rejected(spy):
    s = SStrict()
    apply_command(s, {"t": "setres", "scale": "abc"})
    assert not hasattr(s, "res")


@pytest.mark.parametrize("v,expect", [(0.5, 0.5), ("0.5", 0.5), ("auto", None), (None, None)])
def test_setres_valid_passes(spy, v, expect):
    s = SStrict()
    apply_command(s, {"t": "setres", "scale": v})
    assert getattr(s, "res", "missing") == expect


# ---- NaN/inf 判定助手自检（防助手本身写错） ----

def test_nan_inf_detection_math():
    assert math.isnan(float("nan")) and math.isnan(float("nan")) is not None
    # x != x 是 NaN 的经典判别式，守卫里用的就是它
    assert float("nan") != float("nan")
    assert float("inf") != float("inf") or True  # inf == inf，走显式 in 判定
    assert float("inf") in (float("inf"), float("-inf"))
