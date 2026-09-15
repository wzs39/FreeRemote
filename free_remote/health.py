# -*- coding: utf-8 -*-
"""健康监控：采集失败计数/自愈策略 + 帧率统计。"""
import time

from .config import IS_WIN
from .logging_util import log

def is_screen_locked() -> bool:
    """Windows 下检测屏幕是否锁定（锁屏时无法注入输入）。启发式，仅作提示。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not h:
            return True
        u.CloseDesktop(h)
        return False
    except Exception:
        return False


class HealthMonitor:
    """运行时健康监控 + 自动补救（自检/自我审查/自愈）。"""

    def __init__(self, target_fps: int):
        self.target_fps = target_fps
        self.start = time.monotonic()
        self.capture_ok = 0
        self.capture_fail = 0
        self.consecutive_fail = 0
        self.last_err = None
        self.reconnects = 0
        self.fps_ema = 0.0
        self.last_frame_t = None
        self.degraded = False
        self.remedies = []  # [{at, action}] 自动补救记录
        self._last_reinit = 0.0  # 上次重建采集器时刻（防抖）
        self.reinit_count = 0
        # GIL 卡顿检测：采集耗时异常的样本（dt 超过目标周期 2.5 倍）
        self.slow_notes: list[float] = []
        self._gil_warned_at = 0.0
        self.capture_pauses = 0
        self.paused_until = 0.0
        self._expected_dt = 1.0 / max(target_fps, 1) * 2.5

    # ---- 采集统计 ----
    def note_ok(self):
        now = time.monotonic()
        if self.last_frame_t:
            dt = now - self.last_frame_t
            inst = 1.0 / dt if dt > 0 else 0.0
            self.fps_ema = self.fps_ema * 0.8 + inst * 0.2 if self.fps_ema else inst
            if dt > self._expected_dt:  # 本帧采集耗时异常（GIL 被占用的典型信号）
                self.slow_notes.append(now)
                cutoff = now - 4.0
                self.slow_notes = [t for t in self.slow_notes if t > cutoff]
        self.last_frame_t = now
        self.capture_ok += 1
        self.consecutive_fail = 0

    def note_fail(self, exc):
        self.capture_fail += 1
        self.consecutive_fail += 1
        self.last_err = str(exc)[:120]

    def note_reconnect(self):
        self.reconnects += 1

    # ---- GIL 争用自保（minimize 重型应用 / 编译 / 压缩时） ----
    def check_gil_pause(self, streamer):
        """无观看者且 GIL 持续卡顿 → 暂停采集 5 秒，把 CPU 让给前台应用。"""
        now = time.monotonic()
        if now < self.paused_until:
            return True  # 暂停中
        if len(self.slow_notes) >= 3 and (streamer is None or streamer.viewer_count() == 0):
            self.paused_until = now + 5.0
            self.capture_pauses += 1
            self.slow_notes = []
            if now - self._gil_warned_at > 30:
                self._gil_warned_at = now
                self.add_remedy("检测到系统繁忙且无观看者，暂停屏幕采集 5 秒让出 CPU")
            return True
        return False

    def add_remedy(self, action: str):
        self.remedies.append({"at": time.strftime("%H:%M:%S"), "action": action})
        log("WARN", f"自愈：{action}")

    # ---- 自动补救 ----
    def maybe_heal(self, streamer):
        """采集连续失败→尽快重建采集器（3 秒防抖）；实际帧率过低→自动降一档画质。

        BitBlt 失败典型场景：锁屏/安全桌面切换/显示驱动重置。连续 2 次即重建
        （旧值 5 在 8fps 下要 0.6 秒才触发，且主推流路径此前根本没接本函数），
        重建限频 3 秒一次防止持续失败时无意义刷屏。
        """
        if streamer is None:
            return
        now = time.monotonic()
        if self.consecutive_fail >= 2 and now - self._last_reinit > 3.0:
            self._last_reinit = now
            self.reinit_count += 1
            streamer.reinit()
            if streamer.probe_capture():
                self.consecutive_fail = 0
                self.add_remedy(f"屏幕采集连续失败，已自动重建采集器（第 {self.reinit_count} 次）")
            else:
                # 重建后仍探不通（锁屏/安全桌面）——静默重试，不刷日志不刷 remedy
                self.consecutive_fail = 1
        if (self.fps_ema and self.fps_ema < self.target_fps * 0.5
                and not self.degraded and streamer.preset != "low"):
            order = ["high", "mid", "low"]
            idx = order.index(streamer.preset)
            if idx < len(order) - 1:
                streamer.set_preset(order[idx + 1])
                self.degraded = True
                self.add_remedy(f"实际帧率 {self.fps_ema:.1f}fps 低于目标 {self.target_fps}fps，"
                                f"已自动降为「{streamer.preset}」画质")

    # ---- 自检报告（供 /status 与手机端自检页） ----
    def report(self, streamer=None, mode="lan", no_auth=False, token_len=0, ws_ok=None):
        total = self.capture_ok + self.capture_fail
        fps_now = round(self.fps_ema, 1)
        checks = []

        cap_ok = total == 0 or self.consecutive_fail < 5
        checks.append({
            "id": "capture", "name": "屏幕采集", "ok": cap_ok,
            "detail": f"成功 {self.capture_ok} 次 / 失败 {self.capture_fail} 次"
                       + (f"（最近错误：{self.last_err}）" if self.last_err else ""),
            "remedy": "macOS：系统设置→隐私与安全性→屏幕录制 授权；"
                       "Windows：检查是否锁屏/远程桌面会话已断开",
        })
        fps_ok = total == 0 or (self.fps_ema >= self.target_fps * 0.5)
        checks.append({
            "id": "fps", "name": "推流帧率", "ok": fps_ok,
            "detail": f"实际 {fps_now} fps / 目标 {self.target_fps} fps"
                       + ("（已自动降画质）" if self.degraded else ""),
            "remedy": "在手机端切到「流畅」画质，或调低 --fps；检查网络带宽",
        })
        checks.append({
            "id": "conn", "name": "控制通道", "ok": ws_ok is not False,
            "detail": "已连接" if ws_ok else (("中继已连接" if mode == "relay" else "局域网模式") if ws_ok is None else "已断开"),
            "remedy": "检查电脑与手机是否同一网络；Windows 防火墙是否放行端口；中继是否可达",
        })
        checks.append({
            "id": "quality", "name": "画质档位", "ok": not self.degraded,
            "detail": f"当前 {streamer.preset if streamer else '?'}"
                       + ("（已自动降档）" if self.degraded else ""),
            "remedy": "网络恢复后可在手机端手动切回「均衡/高清」",
        })
        if is_screen_locked():
            checks.append({
                "id": "lock", "name": "屏幕状态", "ok": False,
                "detail": "检测到屏幕可能已锁定", "remedy": "先解锁电脑屏幕再操作（锁屏时无法注入）",
            })

        warnings = []
        if no_auth:
            warnings.append("口令校验已关闭（--no-auth），仅限完全可信的局域网")
        if token_len and token_len < 8:
            warnings.append(f"当前口令仅 {token_len} 位，建议至少 8 位（--token）")
        if self.degraded:
            warnings.append("已自动降画质，可手动切回更高画质")

        return {
            "ok": all(c["ok"] for c in checks),
            "mode": mode,
            "uptime": int(time.monotonic() - self.start),
            "fps_target": self.target_fps,
            "fps_actual": fps_now,
            "preset": streamer.preset if streamer else "",
            "frame": list(streamer.frame_size) if streamer else None,
            "reconnects": self.reconnects,
            "warnings": warnings,
            "checks": checks,
            "remedies": list(self.remedies[-10:]),
        }

