# -*- coding: utf-8 -*-
"""injection.py — 输入注入基础设施层。

引擎选择 + 统一调度门面 `_INJ`（pyautogui 兼容接口）：
  - Windows: win_input（SendInput 直通，微秒级，修饰键状态由其唯一持有）
  - 其它平台 / win_input 导入失败: pyautogui 自动回退

本模块不依赖 capture/command——依赖方向从此自上而下：
  capture、command、web、relay → injection → win_input
"""
import sys

import pyautogui

pyautogui.FAILSAFE = False  # 远程控制时关闭"鼠标移到左上角即中止"的安全熔断
# 关键：pyautogui 默认每次注入后 sleep 0.1s（实测每次移动/点击延迟 100ms）。
# 远程控制必须即时响应，置 0（Windows 主路径走 SendInput 不受影响，此为回退路径保底）。
pyautogui.PAUSE = 0

try:
    from . import win_input as _win
except Exception:  # pragma: no cover - 非 Windows 或导入失败
    _win = None

_WIN_USABLE = bool(_win and getattr(_win, "available", False))

# 统一调度点：所有鼠标/键盘注入都经 _INJ；Windows 下修饰键状态由
# win_input._held 唯一持有（release_all 只释放真正按住的键）。
INJ = _win if _WIN_USABLE else pyautogui

IS_WIN_ENGINE = _WIN_USABLE


def force_release_all_keys(reason: str = "") -> int:
    """强制释放本进程注入且尚未抬起的全部键（含非修饰键，如游戏里按住的 w）。"""
    return _force_release(reason, "全部按住键")


def force_release_all_mods(reason: str = "") -> int:
    """旧名兼容：断线/解锁键盘实际全量释放（含非修饰键）。"""
    return force_release_all_keys(reason)


def _force_release(reason: str, what: str) -> int:
    """Windows 引擎：只释放 _held 里真正按住的键（不盲发，不误伤物理按键）。
    pyautogui 回退：盲发常用键 keyUp 兜底。返回释放的按住状态数（供日志）。"""
    if _WIN_USABLE:
        n = _win.release_all()
    else:
        n = 0
        for k in ("ctrl", "alt", "shift", "win"):
            try:
                pyautogui.keyUp(k)
            except Exception:
                pass
    if reason:
        from .logging_util import log
        log("INFO", f"已强制释放{what}（{reason}，{n} 个按住状态），防卡键")
    return n


if not _WIN_USABLE:  # pragma: no cover - 非 Windows 才走
    import sys as _sys
    if _sys.platform.startswith("win"):
        raise RuntimeError("win_input 在 Windows 上导入失败，请检查依赖")
