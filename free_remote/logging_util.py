# -*- coding: utf-8 -*-
"""统一日志：控制台 + UTF-8 落盘。"""
import time

from .config import LOG_FILE

def log(level: str, msg: str):
    """统一日志：控制台打印 [时间] [级别] 消息，同时强制 UTF-8 落盘 server.log。
    level 取值：INFO / WARN / ERROR（错误行统一带 [ERROR]，可直接 grep）。"""
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
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass

