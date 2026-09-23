#!/usr/bin/env python3
"""FreeRemote 看门狗：让服务"一直可用"。

三种保障：
1. 单实例锁 —— 同一目录只允许一个看门狗（.freebuff/hot.pid + 存活校验）。
   历史事故：两个看门狗并存时互相拉起/互杀，服务反复重启后全灭。
2. 崩溃自动重启 —— server.py 意外退出（崩溃/被误杀）后 2 秒内自动拉起，
   手机端 1.5 秒重连机制会让页面自动恢复，无需人工干预。
3. 文件热更新 —— 监听 server.py / relay.py / token.txt / 前端模块变化，
   发现修改就平滑重启服务进程：改动即生效，手机自动重连。

自身动作（启动/重启/热更新/子进程退出码）全部写入 logs/watchdog.log ——
排障时先看这个文件（server.log 只含服务自身日志，看不到看门狗行为）。

用法：
    python hot.py [server.py 的任意参数]
    python hot.py                 # 默认参数启动（读 token.txt）
    python hot.py --once          # 热更新 + 一次性口令
    python hot.py --relay ...     # 识别码模式同样受保护

退出：Ctrl+C 停止看门狗和服务；双击 stop.bat 会连看门狗一起停。
约定：server.py 以退出码 3 结束时看门狗不重启（用于明确的"要求退出"）。
"""

import atexit
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WATCH_FILES = ["server.py", "token.txt", "web/index.html", "web/js/state.js", "web/js/proto.js", "web/js/info.js", "web/js/quality.js",
               "web/js/ctl.js", "web/js/gestures.js", "web/js/control.js",
               "web/js/files.js", "web/js/prefs.js", "web/js/bigmode.js",
               "web/js/keyboard.js", "web/js/login.js", "web/js/video.js",
               "web/js/view.js", "web/js/mjpeg.js", "web/js/main.js",
               "free_remote/config.py", "free_remote/capture.py", "free_remote/command.py",
               "free_remote/injection.py",
               "free_remote/web.py", "free_remote/webcore.py", "free_remote/relay.py",
               "free_remote/fileshare.py", "free_remote/health.py", "free_remote/netinfo.py",
               "free_remote/tokens.py", "free_remote/win_input.py", "free_remote/logging_util.py"]
PID_FILE = BASE_DIR / ".freebuff" / "hot.pid"   # 与 stop.bat 共用：指向活着的看门狗
LOG_PATH = BASE_DIR / "logs" / "watchdog.log"
RESTART_DELAY = 2.0          # 崩溃后等待秒数
POLL_INTERVAL = 2.0          # 文件变化轮询间隔
FAST_FAIL_LIMIT = 5          # 连续快速崩溃次数上限（防止死循环拉起）
FAST_FAIL_WINDOW = 3.0       # 启动后多少秒内退出算"快速崩溃"
LOG_ROTATE_BYTES = 2 * 1024 * 1024   # watchdog.log 超过 2MB 时在启动时裁剪
LOG_KEEP_BYTES = 256 * 1024          # 裁剪后保留的尾部大小

_logf = None


def now() -> str:
    return time.strftime("%H:%M:%S")


def say(msg: str) -> None:
    line = f"[watchdog {now()}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    if _logf is not None:
        try:
            _logf.write(line + "\n")
        except Exception:
            pass


def _rotate_log() -> None:
    """启动时裁剪过大的自身日志（服务运行期间不碰文件，避免句柄竞争）。"""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_ROTATE_BYTES:
            data = LOG_PATH.read_bytes()[-LOG_KEEP_BYTES:]
            LOG_PATH.write_bytes("...(旧日志已裁剪)...\n".encode("utf-8") + data)
    except OSError:
        pass


def _open_log() -> None:
    global _logf
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _logf = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        _logf = None


def _pid_alive(pid: int) -> bool:
    """pid 是否为存活的 python 进程（校验进程名，防 PID 复用误判）。"""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10).stdout
        else:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=10).stdout
        return "python" in out.lower()
    except Exception:
        return False


def acquire_lock() -> bool:
    """单实例锁：已有活着的看门狗则返回 False。锁文件与 stop.bat 共用。"""
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if PID_FILE.exists():
            raw = PID_FILE.read_text(encoding="utf-8", errors="replace").strip()
            if raw.isdigit() and _pid_alive(int(raw)):
                return False
            say(f"发现过期 pid 记录（{raw or '空'}，进程已不在），清掉重上锁")
    except OSError:
        pass
    PID_FILE.write_text(str(_own_pid()), encoding="utf-8")
    return True


def _own_pid() -> int:
    import os
    return os.getpid()


def release_lock() -> None:
    """仅当锁文件仍指向自己时删除（避免误删后启动者的记录）。"""
    try:
        if PID_FILE.exists() and PID_FILE.read_text(
                encoding="utf-8", errors="replace").strip() == str(_own_pid()):
            PID_FILE.unlink()
    except OSError:
        pass


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


def _stop_child(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    _rotate_log()
    _open_log()
    if not acquire_lock():
        say(f"已有看门狗在运行（pid {PID_FILE.read_text().strip()}），本次拒绝启动，退出。")
        say("若确认无看门狗属误判：双击 stop.bat 清理后再启动。")
        return 0
    atexit.register(release_lock)

    child_args = sys.argv[1:]
    say(f"看门狗启动（目录 {BASE_DIR}，pid {_own_pid()}）")
    say("功能：单实例锁 + 崩溃自动重启 + 文件热更新（改动即生效）")
    say("停止：Ctrl+C，或双击 stop.bat（会连看门狗一起停）")

    mtimes = snapshot_mtimes()
    fast_fails = 0
    proc = None
    try:
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
                        _stop_child(proc)
                        time.sleep(1.0)
                        hot = True
                        break
                if hot:
                    continue  # 热更新：直接进入下一轮 spawn（不算崩溃）
            except KeyboardInterrupt:
                say("收到停止信号，正在停止服务…")
                _stop_child(proc)
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
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
