# -*- coding: utf-8 -*-
"""断线全量释放：普通键（非修饰键）按住后断线也必须被释放。

真实事故（审计范围外发现）：手机发 {"t":"keydown","key":"w"} 后断线，
release_all 只清修饰键，'w' 在电脑上一直按着（打字/游戏持续触发）直到
物理触碰。三层修复各锁一条：
  1. win_input.key()  —— 非修饰键按下也入册 _held（唯一事实来源）
  2. web ws_handler   —— 断线 finally 全量释放（web 模式）
  3. relay ctl()      —— 手机断线合成 releasekeys 下发（中继模式，
                         PC 端连接由心跳维持、对手机断线不可见）
本文件锁 1 与释放语义；2/3 的接线由 test_command 的路由锁 + 源码依赖锁覆盖。
"""
import pytest


@pytest.fixture
def win():
    w = pytest.importorskip("free_remote.win_input")
    if not getattr(w, "available", False):
        pytest.skip("非 Windows")
    w.release_all()  # 隔离起点
    yield w
    w.release_all()  # 不向真实桌面泄漏按住状态


def test_non_modifier_key_tracked_and_released(win):
    """按住的普通键（如游戏键 w）必须入册并在 release_all 时抬起。"""
    win.key("w", up=False)
    assert win.is_held("w")
    n = win.release_all()
    assert n >= 1
    assert not win.is_held("w")


def test_release_all_covers_mixed_keys(win):
    """修饰键 + 普通键混按，断线释放一个不留。"""
    win.key("ctrl", up=False)
    win.key("w", up=False)
    win.key("f5", up=False)
    n = win.release_all()
    assert n == 3
    assert not win.is_held("ctrl") and not win.is_held("w") and not win.is_held("f5")
    assert win.release_all() == 0  # 幂等：第二次不盲发


def test_normal_press_does_not_leak(win):
    """press（down+up 成对）不应在 _held 留痕——只有按住的键才入册。"""
    win.press("a")
    assert not win.is_held("a")
    assert win.release_all() == 0


def test_releasekeys_reason_passthrough(monkeypatch):
    """releasekeys 指令的 reason 透传（中继断线下发的来源标识可达日志）。"""
    from free_remote import command

    seen = []
    monkeypatch.setattr(command, "force_release_all_keys", lambda r="": seen.append(r))
    command.apply_command(object(), {"t": "releasekeys", "reason": "手机断线(中继下发)"})
    assert seen == ["手机断线(中继下发)"]


def test_web_disconnect_wires_full_release():
    """web 模式断线走全量释放入口（源码依赖锁，防回退到旧 mods-only 名）。"""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "free_remote" / "web.py"
    text = src.read_text(encoding="utf-8")
    assert "force_release_all_keys" in text
    assert "force_release_all_mods" not in text, "web 断线不得再走 mods-only 旧名"


def test_relay_server_synth_release_on_phone_disconnect():
    """中继服务器在手机断线 finally 合成 releasekeys 下发（源码依赖锁）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "relay.py").read_text(encoding="utf-8")
    assert '"t": "releasekeys", "reason": "手机断线(中继下发)"' in src
    # 下发点必须在 ctl 处理器的 async for 之后、会话结束日志之前（手机断线才触发）
    ctl = src[src.index("async def ctl"):]
    assert ctl.index("async for msg in ws") < ctl.index("releasekeys") < ctl.index("手机会话结束")
