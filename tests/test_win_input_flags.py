# -*- coding: utf-8 -*-
"""SendInput 鼠标标志位锁：DOWN/UP 常量必须与 Windows 定义一致。

历史 bug：_BTN_FLAGS 曾把三组标志平移一格（左=(LEFTUP,RIGHTDOWN)...），
手机左键点击实际是"右键按下且永不抬起"——点按变右键、电脑左键被锁死、
目标图标卡在按压态。此文件锁死常量防再犯。
"""
from free_remote import win_input


def test_left_button_flags():
    # MOUSEEVENTF_LEFTDOWN=0x0002 LEFTUP=0x0004
    assert win_input._BTN_FLAGS["left"] == (0x0002, 0x0004)


def test_right_button_flags():
    # MOUSEEVENTF_RIGHTDOWN=0x0008 RIGHTUP=0x0010
    assert win_input._BTN_FLAGS["right"] == (0x0008, 0x0010)


def test_middle_button_flags():
    # MOUSEEVENTF_MIDDLEDOWN=0x0020 MIDDLEUP=0x0040
    assert win_input._BTN_FLAGS["middle"] == (0x0020, 0x0040)


def test_wheel_flags_untouched():
    # 滚轮独立于按钮标志：WHEEL=0x0800 HWHEEL=0x1000（若改 click 表不波及 scroll）
    import ctypes
    assert 0x0800 not in sum(win_input._BTN_FLAGS.values(), ())
    assert 0x1000 not in sum(win_input._BTN_FLAGS.values(), ())


def test_click_emits_paired_down_up():
    """注入实发：click 左键必须先 LEFTDOWN 再 LEFTUP，成对且有序（move 消息不算）。"""
    import ctypes
    sent = []
    orig = win_input.user32.SendInput

    def spy(n, buf, size):
        inp = ctypes.cast(buf, ctypes.POINTER(win_input._INPUT)).contents
        sent.append((inp.type, inp.mi.dwFlags))
        return 1

    win_input.user32.SendInput = spy
    try:
        win_input.click(100, 100, button="left")
    finally:
        win_input.user32.SendInput = orig
    clicks = [f for (typ, f) in sent if typ == 0]  # INPUT_MOUSE=0
    # click 序列：[move] → LEFTDOWN → LEFTUP；首条按钮消息是 DOWN，末条是 UP
    assert 0x0002 in clicks, f"缺 LEFTDOWN: {[hex(f) for f in clicks]}"
    assert 0x0004 in clicks, f"缺 LEFTUP: {[hex(f) for f in clicks]}"
    assert clicks.index(0x0002) < clicks.index(0x0004), "DOWN 必须先于 UP"
    assert not (0x0008 in clicks or 0x0010 in clicks or 0x0020 in clicks), \
        f"左键点击混入其他按钮标志: {[hex(f) for f in clicks]}"
