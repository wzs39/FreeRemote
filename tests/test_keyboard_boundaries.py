# -*- coding: utf-8 -*-
"""键盘类指令边界对抗：keydown/keyup/press/combo/text 的畸形输入面。

服务端契约（dispatch 层校验后，非法输入在 _INJ 之前拒绝）：
  - key 类：cmd["key"] 必须为 1..32 字符的 str（丢字段/非 str/空/超长/未知键名拒绝）
  - combo：cmd["keys"] 必须为 1..6 个 str 键名的列表（字符串会被按字符迭代——逐字符
    注入是真实危害，非理论；上限防 DoS）
  - text：必须为 1..2000 字符的 str（镜像前端 proto.js 契约；此前无上限，
    4M 字符 × interval=0.01 ≈ 11 小时连续击键占用注入线程）
  - 输出消息为白名单重组：嵌套/多余字段（含深嵌套 payload）不透传
"""
import pytest

from free_remote import command
from free_remote.command import apply_command


@pytest.fixture
def spy(monkeypatch):
    class Spy:
        def __init__(self):
            self.calls = []

        def keyDown(self, k):
            self.calls.append(("keyDown", k))

        def keyUp(self, k):
            self.calls.append(("keyUp", k))

        def press(self, k):
            self.calls.append(("press", k))

        def typewrite(self, text, interval=0.0):
            self.calls.append(("typewrite", text, interval))

        def hotkey(self, *keys):
            self.calls.append(("hotkey",) + keys)

        def moveTo(self, x, y):
            pass

        def click(self, x, y, button="left", clicks=1):
            pass

        def scroll(self, dy):
            pass

        def hscroll(self, dx):
            pass

    s = Spy()
    monkeypatch.setattr(command, "_INJ", s)
    return s


class S:
    """极简 streamer 桩：apply_command 只在 setpreset/setres 用到它。"""

    def set_preset(self, p):
        pass

    def set_resolution(self, v):
        pass


# ---- keydown/keyup/press：key 字段守卫 ----

def test_keydown_valid_reaches_engine(spy):
    apply_command(S(), {"t": "keydown", "key": "w"})
    assert spy.calls == [("keyDown", "w")]


def test_keyup_valid_reaches_engine(spy):
    apply_command(S(), {"t": "keyup", "key": "w"})
    assert spy.calls == [("keyUp", "w")]


def test_press_valid_reaches_engine(spy):
    apply_command(S(), {"t": "press", "key": "enter"})
    assert spy.calls == [("press", "enter")]


@pytest.mark.parametrize("cmd", [
    {"t": "keydown"},                                  # 丢 key
    {"t": "keydown", "key": 123},                      # 非字符串
    {"t": "keydown", "key": None},
    {"t": "keydown", "key": ["w"]},                    # 列表
    {"t": "keydown", "key": ""},                       # 空
    {"t": "keydown", "key": "x" * 33},                 # 超长（>32）
    {"t": "press", "key": {"deep": {"nested": 1}}},    # dict
])
def test_key_malformed_rejected_before_engine(spy, cmd):
    apply_command(S(), cmd)
    assert spy.calls == [], f"畸形 key 应在引擎前拒绝：{cmd}"


def test_key_oversize_boundary_32_ok(spy):
    apply_command(S(), {"t": "keydown", "key": "k" * 32})
    assert spy.calls == [("keyDown", "k" * 32)]  # 尺寸合规，未知键名由引擎层报错


def test_key_unknown_name_reaches_engine(spy):
    """形状合规的未知键名由引擎层权威判定（_resolve_vk 返回 None → 注入前 ValueError），
    dispatch 只管形状，不重复维护键名全域。"""
    apply_command(S(), {"t": "keydown", "key": "notakey"})
    assert spy.calls == [("keyDown", "notakey")]


# ---- combo：形状守卫（字符串会按字符迭代逐键注入）----

def test_combo_valid(spy):
    apply_command(S(), {"t": "combo", "keys": ["ctrl", "alt", "del"]})
    assert spy.calls == [("keyDown", "ctrl"), ("keyDown", "alt"), ("press", "del"),
                         ("keyUp", "alt"), ("keyUp", "ctrl")]


def test_combo_string_is_not_iterated(spy):
    """字符串 "win" 曾被按字符迭代 → 逐字符按键注入。必须整体拒绝。"""
    apply_command(S(), {"t": "combo", "keys": "win"})
    assert spy.calls == []


@pytest.mark.parametrize("cmd", [
    {"t": "combo"},                              # 丢 keys
    {"t": "combo", "keys": []},                  # 空
    {"t": "combo", "keys": "w"},                 # 单字符字符串（同样按字符迭代）
    {"t": "combo", "keys": {"a": 1}},            # dict
    {"t": "combo", "keys": [123]},               # 非字符串成员
    {"t": "combo", "keys": None},
    {"t": "combo", "keys": ["a"] * 7},           # 超上限（>6）
    {"t": "combo", "keys": [{"deep": [1, 2]}]},  # 嵌套成员
])
def test_combo_malformed_rejected(spy, cmd):
    apply_command(S(), cmd)
    assert spy.calls == []


def test_combo_boundary_6_ok(spy):
    """6 键 = 5 keyDown + 1 press + 5 keyUp = 11 次调用。"""
    apply_command(S(), {"t": "combo", "keys": ["a", "b", "c", "d", "e", "f"]})
    assert len(spy.calls) == 11


# ---- text：长度上限 ----

def test_text_valid_ascii(spy, monkeypatch):
    monkeypatch.setattr(command, "_is_ascii_printable", lambda s: True)
    apply_command(S(), {"t": "text", "text": "hello world"})
    assert spy.calls == [("typewrite", "hello world", 0.01)]


def test_text_empty_rejected(spy):
    apply_command(S(), {"t": "text", "text": ""})
    assert spy.calls == []


def test_text_missing_rejected(spy):
    apply_command(S(), {"t": "text"})
    assert spy.calls == []


def test_text_non_string_rejected(spy):
    apply_command(S(), {"t": "text", "text": 12345})
    assert spy.calls == []


def test_text_oversize_rejected(spy):
    """此前无上限：4M 字符 × interval=0.01 ≈ 11 小时连续击键。现镜像前端 2000 上限。"""
    apply_command(S(), {"t": "text", "text": "A" * 2001})
    assert spy.calls == []


def test_text_boundary_2000_ok(spy, monkeypatch):
    monkeypatch.setattr(command, "_is_ascii_printable", lambda s: True)
    apply_command(S(), {"t": "text", "text": "A" * 2000})
    assert spy.calls == [("typewrite", "A" * 2000, 0.01)]


def test_text_deep_nested_payload_not_typed(spy, monkeypatch):
    """嵌套字段（dict/list）不得被当作文本敲出。"""
    apply_command(S(), {"t": "text", "text": {"deep": {"deeper": [1, 2, 3]}}})
    assert spy.calls == []


# ---- 白名单重组：多余/嵌套字段不透传（以 press 为例，输出经 json 往返验证）----

def test_extra_nested_fields_dropped(spy):
    apply_command(S(), {"t": "press", "key": "enter",
                        "junk": {"deep": {"deeper": {"deepest": [1, 2, 3]}}}})
    assert spy.calls == [("press", "enter")]
