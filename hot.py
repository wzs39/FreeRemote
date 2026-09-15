#!/usr/bin/env python3
"""FreeRemote 看门狗：让服务"一直可用"。

两种保障：
1. 崩溃自动重启 —— server.py 意外退出（崩溃/被误杀）后 2 秒内自动拉起，
   手机端 1.5 秒重连机制会让页面自动恢复，无需人工干预。
2. 文件热更新 —— 监听 server.py / relay.py / token.txt 变化，发现修改就
   平滑重启服务进程：改动即生效（热更新），手机自动重连。

用法：
    python hot.py [server.py 的任意参数]
    python hot.py                 # 默认参数启动（读 token.txt）
    python hot.py --once          # 热更新 + 一次性口令
    python hot.py --relay ...     # 识别码模式同样受保护

退出：Ctrl+C 停止看门狗和服务。
约定：server.py 以退出码 3 结束时看门狗不重启（用于明确的"要求退出"）。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WATCH_FILES = ["server.py", "relay.py", "token.txt", "web/index.html"]
RESTART_DELAY = 2.0          # 崩溃后等待秒数
POLL_INTERVAL = 2.0          # 文件变化轮询间隔
FAST_FAIL_LIMIT = 5          # 连续快速崩溃次数上限（防止死循环拉起）
FAST_FAIL_WINDOW = 3.0       # 启动后多少秒内退出算"快速崩溃"


def now() -> str:
    return time.strftime("%H:%M:%S")


def say(msg: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"[watchdog {now()}] {msg}", flush=True)


def snapshot_mtimes() -> dict:
    out = {}
    for name in WATCH_FILES:
        p = BASE_DIR / name
        try:
            out[name] = p.stat().st_mtime
        except OSError:
            out[name] = None
    return out


def spawn(child_args) -> subprocess.Popen:
    cmd = [sys.executable, str(BASE_DIR / "server.py")] + list(child_args)
    return subprocess.Popen(cmd, cwd=str(BASE_DIR))


def main() -> int:
    child_args = sys.argv[1:]
    say(f"看门狗启动（目录 {BASE_DIR}）")
    say("功能：崩溃自动重启 + 文件热更新（server.py / relay.py / token.txt / 前端页面 改动即生效）")
    say("停止：按 Ctrl+C（会同时停止 FreeRemote 服务）")

    mtimes = snapshot_mtimes()
    fast_fails = 0
    proc = None
    while True:
        start_ts = time.time()
        try:
            proc = spawn(child_args)
            say(f"服务已启动（pid {proc.pid}）")
            # 子进程运行期间持续轮询文件变化 —— 改动即热更新（无需等子进程退出）
            hot = False
            while proc.poll() is None:
                time.sleep(POLL_INTERVAL)
                changed = [n for n, m in snapshot_mtimes().items() if m != mtimes.get(n)]
                if changed:
                    mtimes = snapshot_mtimes()
                    fast_fails = 0
                    say(f"检测到文件更新（{', '.join(changed)}）→ 热更新：重启服务…")
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    time.sleep(1.0)
                    hot = True
                    break
            if hot:
                continue  # 热更新：直接进入下一轮 spawn（不算崩溃）
        except KeyboardInterrupt:
            say("收到停止信号，正在停止服务…")
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            say("已全部停止")
            return 0
        except Exception as e:  # spawn 失败（如 python 路径问题）也要兜底
            say(f"启动服务失败：{e}，{RESTART_DELAY:.0f} 秒后重试")
            time.sleep(RESTART_DELAY)
            continue

        code = proc.returncode
        ran = time.time() - start_ts

        if code == 3:
            say("服务请求退出（exit 3），看门狗停止")
            return 0

        if ran < FAST_FAIL_WINDOW:
            fast_fails += 1
            if fast_fails >= FAST_FAIL_LIMIT:
                # 不再永久放弃：退避后重置计数继续尝试（端口释放/依赖恢复后自愈）
                backoff = min(60.0, RESTART_DELAY * (2 ** FAST_FAIL_LIMIT))
                say(f"服务连续 {fast_fails} 次快速退出（exit {code}），"
                    f"{backoff:.0f} 秒后继续尝试（端口未释放/配置问题会自动恢复）")
                fast_fails = 0
                time.sleep(backoff)
                continue
            say(f"服务异常退出（exit {code}，运行 {ran:.1f}s），"
                f"{RESTART_DELAY:.0f} 秒后自动重启（{fast_fails}/{FAST_FAIL_LIMIT}）…")
        else:
            fast_fails = 0
            say(f"服务退出（exit {code}），{RESTART_DELAY:.0f} 秒后自动重启…")
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    sys.exit(main())
