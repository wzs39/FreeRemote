# -*- coding: utf-8 -*-
"""控制指令：手机指令 -> 系统输入注入（Windows 走 SendInput，其余 pyautogui）。"""
import pyautogui
import pyperclip

from .config import IS_MAC
from .logging_util import log
from . import win_input

Streamer = None  # 仅类型注解用；运行时由 web/relay 传入实例（避免循环导入）

# ---------------------------------------------------------------- 控制指令

def _is_ascii_printable(s: str) -> bool:
    """可打印 ASCII（字母/数字/空格/常见符号）→ 可直接逐键敲出，不依赖剪贴板。"""
    return bool(s) and all(not (ord(c) < 0x20 or ord(c) > 0x7E) for c in s)


def _paste_keys():
    """粘贴组合键：macOS 用 Cmd+V，其它平台用 Ctrl+V。"""
    return ["command", "v"] if IS_MAC else ["ctrl", "v"]


# ---------------- 输入注入引擎（Windows: SendInput 直通；其它: pyautogui） ----------------
# 统一调度点：所有鼠标/键盘注入都经 _INJ；Windows 下修饰键状态由
# win_input._held 唯一持有（release_all 只释放真正按住的键）。
_INJ = win_input if (win_input and win_input.available) else pyautogui


def _with_mods(mods, fn):
    """按住修饰键执行动作（如 Ctrl+点击）。"""
    for k in mods:
        _INJ.keyDown(k)
    try:
        fn()
    finally:
        for k in reversed(mods):
            _INJ.keyUp(k)


_MOD_KEYS = ("ctrl", "alt", "shift", "win")


def force_release_all_mods(reason: str = ""):
    """强制释放所有修饰键，防"卡键"。

    场景：手机断线/息屏时 Ctrl 或 Win 正被按住 -> 电脑端键位卡死，
    之后手机的一切点击都变成 Ctrl+点击/Win+点击，表现为"全部失灵"。
    在控制连接断开时调用（websocket 心跳 30s 内必发现断线）。
    Windows 下由 win_input._held 精确释放真正按住的键；其它平台盲发 keyUp 兜底。
    """
    if win_input and win_input.available:
        n = win_input.release_all()
    else:
        n = 0
        for k in _MOD_KEYS:
            try:
                pyautogui.keyUp(k)
            except Exception:
                pass
    if reason:
        log("INFO", f"已强制释放修饰键（{reason}，{n} 个按住状态），防卡键")


def apply_command(streamer: Streamer, cmd: dict):
    """执行一条来自手机的指令。"""
    t = cmd.get("t")
    mods = [k for k in cmd.get("mods", []) if isinstance(k, str)]
    try:
        if t == "move":
            x, y = streamer.frame_to_screen(cmd["x"], cmd["y"])
            _INJ.moveTo(x, y)

        elif t == "click":
            x, y = streamer.frame_to_screen(cmd["x"], cmd["y"])
            button = cmd.get("button", "left")
            clicks = cmd.get("count", 1)
            _with_mods(mods, lambda: _INJ.click(x, y, button=button, clicks=clicks))

        elif t == "scroll":
            dy, dx = cmd.get("dy", 0), cmd.get("dx", 0)
            _with_mods(
                mods,
                lambda: (
                    _INJ.scroll(dy) if dy else None,
                    _INJ.hscroll(dx) if dx else None,
                ),
            )

        elif t == "keydown":
            _with_mods(mods, lambda: _INJ.keyDown(cmd["key"]))
        elif t == "keyup":
            _with_mods(mods, lambda: _INJ.keyUp(cmd["key"]))
        elif t == "press":
            _with_mods(mods, lambda: _INJ.press(cmd["key"]))

        elif t == "combo":
            # 例如 ["ctrl","alt","del"]：按住前面的键，按下最后一个
            keys = cmd["keys"]
            if keys:
                for k in keys[:-1]:
                    _INJ.keyDown(k)
                try:
                    _INJ.press(keys[-1])
                finally:
                    for k in reversed(keys[:-1]):
                        _INJ.keyUp(k)

        elif t == "text":
            text = cmd.get("text", "")
            if text:
                if _is_ascii_printable(text):
                    # 英文/符号：电脑端直接逐键敲出（不需要剪贴板，macOS/弱网更稳）
                    try:
                        _INJ.typewrite(text, interval=0.01)
                    except Exception:
                        pyperclip.copy(text)
                        _INJ.hotkey(*(_paste_keys()))
                else:
                    # 中文等：复制到剪贴板后 Ctrl/Cmd+V 粘贴
                    pyperclip.copy(text)
                    _INJ.hotkey(*_paste_keys())

        elif t == "releasekeys":
            # 手机端"解锁键盘"：修饰键疑似卡住时一键恢复（页面失灵自救）
            force_release_all_mods("手机端请求")

        elif t == "setpreset":
            # 手机端切换画质（中继模式下由电脑端本地编码）
            streamer.set_preset(cmd.get("preset", "mid"))
        elif t == "setres":
            # 手机端自定义推流分辨率（"auto"=恢复跟随画质档位）
            v = cmd.get("scale", None)
            if v in (None, "auto", ""):
                streamer.set_resolution(None)
            else:
                streamer.set_resolution(float(v))

        else:
            log("WARN", f"未知指令：{cmd}")
    except Exception as exc:
        log("ERROR", f"指令失败 [{t}]：{exc}")

