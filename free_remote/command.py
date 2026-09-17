# -*- coding: utf-8 -*-
"""控制指令：手机指令 -> 系统输入注入（Windows 走 SendInput，其余 pyautogui）。"""
import sys

import pyperclip

from .injection import INJ, force_release_all_keys, force_release_all_mods
from .logging_util import log

# 统一注入门面（injection.py 是基础设施层，capture 同样只依赖它）
_INJ = INJ

__all__ = ["apply_command", "force_release_all_keys", "force_release_all_mods", "_INJ"]


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


def _frame_xy(cmd: dict, t: str):
    """校验指令坐标字段，返回 (x, y) 或 None（畸形时 WARN 留痕）。

    拒绝四类：丢字段、非数字类型（str/list/dict/bool 不做隐式转换——
    协议里坐标就是数字，"10" 是违约不是数据）、NaN/inf
    （Python 的 json 扩展语法允许它们进 dict——注入即坐标虚拟化）。
    """
    x, y = cmd.get("x"), cmd.get("y")
    for v in (x, y):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            log("WARN", f"坐标字段缺失或非数字 [{t}]：{cmd}")
            return None
    if x != x or y != y or x in (float("inf"), float("-inf")) or y in (float("inf"), float("-inf")):
        log("WARN", f"坐标非法（NaN/inf）[{t}]：{cmd}")
        return None
    return x, y


def apply_command(streamer, cmd):
    """执行一条来自手机的指令。顶层非对象消息静默拒绝（不炸连接）。

    streamer 只需实现 frame_to_screen/set_preset/set_resolution；
    不写 Streamer 注解：引号内名字仍需导入才能解析（违反 command→capture
    的反向依赖禁令），类型检查交给协议测试的桩。
    """
    if not isinstance(cmd, dict):
        log("WARN", f"非对象消息已拒绝：{cmd!r}")
        return
    t = cmd.get("t")
    mods = [k for k in cmd.get("mods", []) if isinstance(k, str)]
    try:
        if t == "move":
            xy = _frame_xy(cmd, t)
            if xy is None:
                return
            x, y = streamer.frame_to_screen(*xy)
            _INJ.moveTo(x, y)

        elif t == "click":
            xy = _frame_xy(cmd, t)
            if xy is None:
                return
            x, y = streamer.frame_to_screen(*xy)
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
            # 手机端"解锁键盘"/断线自愈：全量释放按住键（含非修饰键，如游戏按住的 w）
            force_release_all_keys(str(cmd.get("reason") or "手机端请求"))

        elif t == "setpreset":
            # 手机端切换画质（中继模式下由电脑端本地编码）
            streamer.set_preset(cmd.get("preset", "mid"))
        elif t == "setres":
            # 手机端自定义推流分辨率（"auto"=恢复跟随画质档位）
            v = cmd.get("scale", None)
            if v in (None, "auto", ""):
                streamer.set_resolution(None)
            else:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    log("WARN", f"分辨率字段非数字：{v!r}")
                    return
                # float("nan")/inf 能溜过 max/min 钳制（nan 不参与比较），必须先挡
                if v != v or v in (float("inf"), float("-inf")):
                    log("WARN", f"分辨率非法（NaN/inf）：{v}")
                    return
                streamer.set_resolution(v)

        else:
            log("WARN", f"未知指令：{cmd}")
    except Exception as exc:
        log("ERROR", f"指令失败 [{t}]：{exc}")
