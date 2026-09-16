# -*- coding: utf-8 -*-
"""win_input.py — Windows 输入注入引擎（ctypes SendInput 直通）。

为什么不用 pyautogui：
  1. pyautogui 每次注入后有 PAUSE 停顿（即使置 0，坐标归一化/安全检查仍有开销）；
  2. 修饰键状态它不持有——卡键只能"盲发 keyUp"，无法判断是否真的按着；
  3. 键名到扫描码的映射是它自己的表，Win 键支持不稳。

本模块是修饰键状态的**唯一所有者**：
  - `_held` 记录当前逻辑按下的修饰键，`release_all()` 只对真正按着的键发 keyUp；
  - 所有注入走 SendInput（无停顿，微秒级）；
  - DPI 感知在导入时开启一次，保证多显示器绝对坐标不因进程 DPI 虚拟化偏移。

非 Windows 平台：`available=False`，server.py 自动回退 pyautogui 路径。
"""
import ctypes
import sys
import time

available = sys.platform.startswith("win")
IS_WIN = available

if available:
    user32 = ctypes.windll.user32
    # DPI 感知：让 moveTo 的绝对坐标与真实物理像素一致（否则 DPI 缩放会偏移）
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
    KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0001

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    # 手机端键名 → VK 码（_NAMED_VK 是大小写不敏感的兜底，其余走 VkKeyScanW）
    _MOD_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
    _NAMED_VK = {
        "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
        "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
        "del": 0x2E, "insert": 0x2D, "ins": 0x2D, "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28,
        "left": 0x25, "right": 0x27, "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
        "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
        "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
        "printscreen": 0x2C, "pause": 0x13, "menu": 0x5D, "apps": 0x5D,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
        "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
        "f11": 0x7A, "f12": 0x7B, "f13": 0x7C, "f14": 0x7D, "f15": 0x7E,
        "f16": 0x7F, "f17": 0x80, "f18": 0x81, "f19": 0x82, "f20": 0x83,
        "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
        "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1,
        "stop": 0xB2, "browserback": 0xA6, "browserforward": 0xA7,
    }
    _EXTENDED_VK = {0x25, 0x26, 0x27, 0x28, 0x21, 0x22, 0x23, 0x24,
                    0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x2C}

    def _resolve_vk(name: str):
        """键名 → VK 码；未知可打印字符走 VkKeyScanW（跟随当前键盘布局）。"""
        n = name.strip().lower()
        if not n:
            return None
        if n in _MOD_VK:
            return _MOD_VK[n]
        if n in _NAMED_VK:
            return _NAMED_VK[n]
        if n.startswith("f") and n[1:].isdigit():
            fi = int(n[1:])
            if 1 <= fi <= 24:
                return 0x70 + (fi - 1)
        if n.startswith("num") and n[3:].isdigit():
            return 0x60 + int(n[3:])
        if len(n) == 1:
            r = user32.VkKeyScanW(ord(n))
            if r != -1:
                return r & 0xFF
            return ord(n.upper())
        return None

    _held = set()  # 本进程逻辑按住的修饰键（唯一事实来源）

    def _send_key(vk, up=False):
        ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
        if not up and vk in _EXTENDED_VK:
            ki.dwFlags |= KEYEVENTF_EXTENDEDKEY
        inp = _INPUT(type=INPUT_KEYBOARD)
        inp.ki = ki
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    # ---------------- 鼠标 ----------------
    def move_to(x, y):
        """绝对坐标移动光标（物理像素，导入时已开启 DPI 感知）。"""
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        nx = int(x * 65535 / max(sw - 1, 1))
        ny = int(y * 65535 / max(sh - 1, 1))
        mi = _MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
        inp = _INPUT(type=INPUT_MOUSE)
        inp.mi = mi
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def cursor_pos():
        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = PT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    # 鼠标按钮 → (DOWN 标志, UP 标志)，Windows 常量：
    #   左 0x0002/0x0004  右 0x0008/0x0010  中 0x0020/0x0040（滚轮 0x0800/0x1000 在 scroll）
    _BTN_FLAGS = {
        "left":   (0x0002, 0x0004),
        "right":  (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }

    def click(x, y, button="left", clicks=1):
        # 严格契约：非法按钮在调度层（command.py）归一化为 left，引擎只接受三键
        if button not in _BTN_FLAGS:
            raise ValueError(f"未知鼠标按钮：{button}")
        flags = _BTN_FLAGS[button]
        for _ in range(max(1, int(clicks))):
            move_to(x, y)
            for flag in flags:
                mi = _MOUSEINPUT(0, 0, 0, flag, 0, None)
                inp = _INPUT(type=INPUT_MOUSE)
                inp.mi = mi
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
                if flag == flags[0]:
                    time.sleep(0.001)  # down→up 间隔，否则部分程序识别不到完整点击

    def scroll(dy=0, dx=0):
        if dy:
            mi = _MOUSEINPUT(0, 0, int(dy) * 120, 0x0800, 0, None)  # WHEEL
            inp = _INPUT(type=INPUT_MOUSE)
            inp.mi = mi
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if dx:
            mi = _MOUSEINPUT(0, 0, int(dx) * 120, 0x1000, 0, None)  # HWHEEL
            inp = _INPUT(type=INPUT_MOUSE)
            inp.mi = mi
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    # ---------------- 键盘（修饰键状态唯一所有者） ----------------
    def mod_down(name: str) -> bool:
        vk = _resolve_vk(name)
        if vk is None:
            return False
        _send_key(vk, up=False)
        _held.add(name.strip().lower())
        return True

    def mod_up(name: str) -> bool:
        n = name.strip().lower()
        vk = _resolve_vk(n)
        if vk is None:
            return False
        _send_key(vk, up=True)
        _held.discard(n)
        return True

    def is_held(name: str) -> bool:
        return name.strip().lower() in _held

    def release_all():
        """只对真正按着的修饰键发 keyUp（盲发反而可能误伤物理按键）。"""
        n = len(_held)
        for k in list(_held):
            mod_up(k)
        return n

    def key(name: str, up=False):
        n = name.strip().lower()
        if n in _MOD_VK:
            # 修饰键统一走持有状态：无论从哪个入口按下，release_all() 都能真正释放
            (mod_up if up else mod_down)(n)
            return
        vk = _resolve_vk(name)
        if vk is None:
            raise ValueError(f"未知键名：{name}")
        _send_key(vk, up=up)

    # ---- pyautogui 兼容接口（server.py 统一经 _INJ 调度） ----
    def moveTo(x, y):
        move_to(x, y)

    def position():
        return cursor_pos()

    def keyDown(name):
        key(name, up=False)

    def keyUp(name):
        key(name, up=True)

    def press(name):
        key(name, up=False)
        key(name, up=True)

    def hscroll(dx):
        scroll(dx=dx)

    def typewrite(text, interval=0.0):
        type_text(text, interval=interval)

    def hotkey(*keys):
        ks = [k for k in keys if k]
        for k in ks:
            key(k, up=False)
        try:
            pass
        finally:
            for k in reversed(ks):
                key(k, up=True)

    def type_text(text: str, interval=0.0):
        for ch in text:
            vk = _resolve_vk(ch)
            if vk is None:
                continue
            r = user32.VkKeyScanW(ord(ch))
            shift_needed = (r & 0xFFFF) != -1 and ((r >> 8) & 0xFF) == 1
            if shift_needed:
                _send_key(0x10, up=False)
            _send_key(vk, up=False)
            _send_key(vk, up=True)
            if shift_needed:
                _send_key(0x10, up=True)
else:  # 非 Windows：占位，server.py 检测 available=False 走 pyautogui
    def move_to(x, y): ...
    def cursor_pos(): return (0, 0)
    def click(x, y, button="left", clicks=1): ...
    def scroll(dy=0, dx=0): ...
    def mod_down(name): return False
    def mod_up(name): return False
    def is_held(name): return False
    def release_all(): return 0
    def key(name, up=False): raise ValueError("win_input 仅支持 Windows")
    def type_text(text, interval=0.0): ...
    # 兼容接口占位（非 Windows 时 _INJ=pyautogui，以下仅为导入完整性）
    moveTo = move_to
    hscroll = scroll
    def position(): return cursor_pos()
    def keyDown(name): ...
    def keyUp(name): ...
    def press(name): ...
    def typewrite(text, interval=0.0): ...
    def hotkey(*keys): ...
