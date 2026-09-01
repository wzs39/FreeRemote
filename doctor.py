#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeRemote 自检工具：自我审查环境与功能，发现问题并给出补救方案。

用法：
  python doctor.py                                    # 常规自检
  python doctor.py --port 8080                        # 顺带检查端口占用
  python doctor.py --relay wss://域名 --id ABC123 --password xxx   # 检查中继连通性
  python doctor.py --test-input                       # 实际测试鼠标注入（会轻微移动鼠标1像素）
"""

import argparse
import asyncio
import importlib
import io
import socket
import sys
import time

# 输出重定向到管道/文件时按 UTF-8 输出，避免 GBK 终端无法编码 ✅⚠❌ 符号
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

PASS = "\u2714"
WARN = "\u26a0"
FAIL = "\u2716"


def section(title):
    print(f"\n=== {title} ===")


def item(ok, name, detail="", remedy=""):
    """打印一项检查。返回 1 表示致命问题（❌），否则 0。"""
    fatal = (not ok) and (not remedy)
    icon = PASS if ok else (WARN if not fatal else FAIL)
    print(f"  {icon} {name}" + (f"：{detail}" if detail else ""))
    if not ok and remedy:
        print(f"     ↳ 补救：{remedy}")
    return 1 if fatal else 0


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def check_env(args):
    errors = 0
    section("环境")
    ver = sys.version_info
    errors += item(ver >= (3, 9), "Python 版本", f"{sys.version.split()[0]}",
                   "需要 Python 3.9+，请升级后重新创建 .venv")
    deps = [("aiohttp", "aiohttp"), ("mss", "mss"), ("PIL", "Pillow"),
            ("pyautogui", "pyautogui"), ("pyperclip", "pyperclip")]
    for mod, name in deps:
        try:
            importlib.import_module(mod)
            errors += item(True, f"依赖 {name}")
        except Exception as e:
            errors += item(False, f"依赖 {name}", str(e)[:60],
                           f"安装依赖：python -m pip install -r requirements.txt")
    return errors


def check_capture(args):
    errors = 0
    section("屏幕采集")
    try:
        import mss
        from PIL import Image
        sct = mss.MSS()
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        t0 = time.time()
        img = sct.grab(mon)
        dt = time.time() - t0
        pil = Image.frombytes("RGB", img.size, img.rgb)
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=60)
        errors += item(True, "采集一帧",
                       f"{img.width}x{img.height}，耗时 {dt*1000:.0f}ms，JPEG {len(buf.getvalue())//1024}KB")
        errors += item(True, "JPEG 编码")
        if args.test_input is False:
            print("     （如怀疑采集卡顿，可在手机端切到「流畅」画质或调低 --fps）")
    except Exception as e:
        errors += item(False, "屏幕采集失败", str(e)[:80],
                       "macOS：系统设置→隐私与安全性→屏幕录制 勾选终端/运行器；"
                       "Windows：确认有活动的桌面会话（非锁屏/远程桌面已断开）")
    if sys.platform.startswith("win"):
        try:
            import ctypes
            u = ctypes.windll.user32
            h = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
            if h:
                u.CloseDesktop(h)
                errors += item(True, "屏幕状态", "桌面会话可访问")
            else:
                errors += item(False, "屏幕可能已锁定", "",
                               "解锁电脑后再操作（锁屏/登录界面时无法注入鼠标键盘）")
        except Exception:
            errors += item(True, "屏幕状态", "无法检测（跳过）")
    return errors


def check_input(args):
    errors = 0
    section("输入注入")
    try:
        import pyautogui
        w, h = pyautogui.size()
        errors += item(True, "pyautogui 可用", f"屏幕 {w}x{h}")
        if args.test_input:
            x, y = pyautogui.position()
            pyautogui.moveTo(x + 1, y, duration=0)
            pyautogui.moveTo(x, y, duration=0)
            errors += item(True, "鼠标注入实测", f"当前位置 ({x},{y})，往返正常")
        if sys.platform == "darwin":
            errors += item(False, "macOS 辅助功能授权", "",
                           "系统设置→隐私与安全性→辅助功能 勾选终端/运行器（未验证，请手动确认）")
    except Exception as e:
        errors += item(False, "pyautogui 不可用", str(e)[:80],
                       "pip install pyautogui；macOS 需辅助功能权限；Windows 需交互式会话")
    return errors


def check_network(args):
    errors = 0
    section("网络")
    errors += item(True, "局域网地址", lan_ip())
    if args.port:
        s = socket.socket()
        s.settimeout(1)
        try:
            s.bind(("0.0.0.0", args.port))
            s.close()
            errors += item(True, "端口", f"{args.port} 空闲，可启动服务")
        except OSError:
            errors += item(False, "端口", f"{args.port} 已被占用",
                           f"换一个端口（--port 8081 等），或用 netstat -ano | findstr :{args.port} 查看占用进程后结束它")
    if args.relay:
        errors += asyncio.run(check_relay(args))
    return errors


async def check_relay(args):
    import aiohttp
    base = args.relay.rstrip("/")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{base}/", timeout=5) as r:
                item(True, "中继可达", f"{base} HTTP {r.status}")
            if args.id and args.password:
                async with s.get(f"{base}/info?id={args.id}&pass={args.password}", timeout=5) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("online"):
                            item(True, "识别码配对", f"{args.id} 在线："
                                 f"{data.get('w','?')}x{data.get('h','?')} {data.get('preset','')}")
                        else:
                            item(False, "识别码配对", f"{args.id} 已登记但设备当前离线",
                                 "检查电脑端 server.py --relay 是否在运行、网络是否可达中继")
                    elif r.status == 403:
                        item(False, "识别码配对", f"{args.id} 口令错误",
                             "核对 --id / --password；如中继为 --strict 模式需先在 --devices 里登记")
                    else:
                        item(False, "识别码配对", f"HTTP {r.status}",
                             "检查中继地址与端口是否正确")
    except Exception as e:
        item(False, "中继连通性", str(e)[:80],
             "确认中继服务器已启动（relay.py）、域名/IP 与端口正确；HTTPS 站点请用 wss://")
    return 0


def check_security(args):
    errors = 0
    section("安全")
    if args.no_auth:
        errors += item(False, "口令校验", "已关闭（--no-auth）",
                       "仅限完全可信的局域网；远程/中继模式请务必关闭 --no-auth 并使用强口令")
    if args.token and len(args.token) < 8:
        errors += item(False, "口令强度", f"长度 {len(args.token)} 位",
                       "口令至少 8 位，建议大小写字母+数字混合（--token）")
    if args.relay:
        if args.password and len(args.password) < 8:
            errors += item(False, "设备口令强度", f"长度 {len(args.password)} 位",
                           "中继口令至少 8 位，并给中继配置 HTTPS（wss）")
    return errors


def main():
    parser = argparse.ArgumentParser(description="FreeRemote 自检工具")
    parser.add_argument("--port", type=int, default=0, help="检查端口是否空闲（如 8080）")
    parser.add_argument("--relay", default="", help="中继地址，如 wss://域名 或 ws://1.2.3.4:9090")
    parser.add_argument("--id", default="", help="识别码模式：设备识别码")
    parser.add_argument("--password", default="", help="识别码模式：设备口令")
    parser.add_argument("--token", default="", help="局域网模式：访问口令")
    parser.add_argument("--no-auth", action="store_true", help="局域网模式：是否关闭了口令校验")
    parser.add_argument("--test-input", action="store_true", help="实际测试鼠标注入（鼠标会移动 1 像素）")
    args = parser.parse_args()

    print("=" * 56)
    print(" FreeRemote 自检 —— 自我审查 + 补救方案")
    print("=" * 56)

    errors = 0
    errors += check_env(args)
    errors += check_capture(args)
    errors += check_input(args)
    errors += check_network(args)
    errors += check_security(args)

    print("\n" + "=" * 56)
    if errors == 0:
        print(" 结论：✅ 全部通过，可以启动服务")
    else:
        print(f" 结论：❌ 发现 {errors} 个致命问题，请按上方「补救」逐项处理")
    print("=" * 56)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
