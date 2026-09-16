# -*- coding: utf-8 -*-
"""控制指令：手机指令 -> 系统输入注入（Windows 走 SendInput，其余 pyautogui）。"""
import sys
from typing import TYPE_CHECKING

import pyperclip

from .injection import INJ, force_release_all_mods
from .logging_util import log

if TYPE_CHECKING:  # 仅类型检查用，运行时零依赖（避免 capture<-command 环）
    from .capture import Streamer

# 统一注入门面（injection.py 是基础设施层，capture 同样只依赖它）
_INJ = INJ

__all__ = ["apply_command", "force_release_all_mods", "_INJ"]


def _with_mods(mods, fn):
    """按住修饰键执行动作（如 Ctrl+点击）。

    只回滚已成功按下的键：若第 k 个键名未知抛出，前 k-1 个仍会在
    finally 里释放，不会滞留为卡键。
    """
    pressed = []
    try:
        for k in mods:
            _INJ.keyDown(k)
            pressed.append(k)
        fn()
    finally:
        for k in reversed(pressed):
            _INJ.keyUp(k)


def _is_ascii_printable(s: str) -> bool:
    """可打印 ASCII（字母/数字/空格/常见符号）→ 可直接逐键敲出，不依赖剪贴板。"""
    return bool(s) and all(not (ord(c) < 0x20 or ord(c) > 0x7E) for c in s)


def _paste_keys():
    """粘贴组合键：macOS 用 Cmd+V，其它平台用 Ctrl+V。"""
    return ["command", "v"] if sys.platform == "darwin" else ["ctrl", "v"]


def apply_command(streamer: "Streamer", cmd: dict):
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
            if button not in ("left", "right", "middle"):
                log("WARN", f"未知鼠标按钮 {button!r}，按 left 处理")
                button = "left"
            try:
                clicks = max(1, min(3, int(cmd.get("count", 1))))
            except (TypeError, ValueError):
                clicks = 1
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
