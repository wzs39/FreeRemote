# -*- coding: utf-8 -*-
"""网络信息：局域网/Tailscale 地址、二维码。"""
import os
import socket
import subprocess

from .logging_util import out

def _qr_text(qr) -> str:
    """把 qrcode 矩阵渲染成纯 ASCII（# 与空格），GBK 终端也能正常显示扫码。"""
    rows = []
    for line in qr.get_matrix():
        rows.append("".join("##" if cell else "  " for cell in line))
    return "\n".join(rows)


def print_qr(url: str, title: str = "用手机相机扫这个二维码"):
    """终端打印二维码 + 链接（依赖 qrcode，可选）；同时伴写日志文件（UTF-8）。"""
    out("")
    out("=" * 58)
    out(f" {title}")
    out(f" {url}")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        out(_qr_text(qr))
    except Exception:
        pass
    out("=" * 58)


def _is_private_lan(ip: str) -> bool:
    """是否私网局域网地址（10.x / 172.16-31.x / 192.168.x），排除回环。"""
    try:
        part = [int(x) for x in ip.split(".")]
        if len(part) != 4:
            return False
        a, b = part[0], part[1]
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
    except Exception:
        return False


def lan_ips() -> tuple[str, list[str]]:
    """返回 (首选IP, 全部候选IP列表)。
    首选 = 默认路由网卡（最可能手机能连）；候选 = 主机名解析出的所有局域网 IPv4。
    """
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if _is_private_lan(ip):
                ips.add(ip)
    except Exception:
        pass
    primary = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
        if _is_private_lan(primary):
            ips.add(primary)
    except Exception:
        pass
    ordered = [primary] + [ip for ip in sorted(ips) if ip != primary]
    return primary, ordered or ["127.0.0.1"]


def tailscale_ips() -> list[str]:
    """探测本机 Tailscale 的 100.x 地址（手机跨网直连用，比局域网 IP 更通用）。"""
    import shutil
    exe = shutil.which("tailscale")
    if not exe:
        for p in (r"C:\Program Files\Tailscale\tailscale.exe",
                  r"D:\Program Files\Tailscale\tailscale.exe",
                  r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
            if os.path.exists(p):
                exe = p
                break
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True,
                             timeout=4).stdout
        return [ip.strip() for ip in out.splitlines() if ip.strip()]
    except Exception:
        return []

