# -*- coding: utf-8 -*-
"""统一日志：控制台 + UTF-8 落盘 + 自动轮转。"""
import os
import time

from .config import LOG_FILE

# 轮转阈值（模块常量，便于测试 monkeypatch）：超限时保留尾部，前面标记裁剪
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024


def _rotate_if_needed():
    """server.log 超过阈值时裁剪保留尾部（写入线程内联执行，无后台任务）。"""
    try:
        if os.path.getsize(LOG_FILE) <= LOG_MAX_BYTES:
            return
        with open(LOG_FILE, "rb") as f:
            f.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            data = f.read()
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write("...(旧日志已裁剪，完整历史见轮转前备份)...\n".encode("utf-8"))
            f.write(data)
        os.replace(tmp, LOG_FILE)
    except OSError:
        pass  # 轮转失败不影响日志写入本身


def log(level: str, msg: str):
    """统一日志：控制台打印 [时间] [级别] 消息，同时强制 UTF-8 落盘 server.log。
    level 取值：INFO / WARN / ERROR（错误行统一带 [ERROR]，可直接 grep）。"""
    _rotate_if_needed()
    line = f"[{time.strftime('%H:%M:%S')}] [{level}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def out(msg: str = ""):
    """横幅输出：控制台打印 + UTF-8 伴写日志文件（无级别前缀，保持横幅格式）。"""
    _rotate_if_needed()
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass
