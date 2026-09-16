# -*- coding: utf-8 -*-
"""FreeRemote 命令行入口。"""
import argparse
import asyncio
import errno
import subprocess
import sys
import time

from aiohttp import web

from .config import BASE_DIR, IS_WIN, LOG_FILE, QUALITY_PRESETS
from .injection import IS_WIN_ENGINE  # 导入时即设置 FAILSAFE/PAUSE=0
from .fileshare import default_share_dir
from .logging_util import log, out
from .netinfo import lan_ips, tailscale_ips
from .relay import random_id, relay_client
from .tokens import gen_token, load_token_file, save_token_file
from .web import make_app

def main():
    # 输出跟随系统编码，仅容错不可编码字符（防 GBK 管道/终端因 emoji 崩溃）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="FreeRemote 手机远程控制电脑")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    parser.add_argument("--fps", type=int, default=8, help="推流帧率 1-30（默认 8）")
    parser.add_argument("--preset", default="mid", choices=list(QUALITY_PRESETS),
                        help="默认画质 low/mid/high（默认 mid）")
    parser.add_argument("--monitor", type=int, default=1, help="显示器编号，1=主屏（默认 1）")
    parser.add_argument("--share-dir", default=default_share_dir(),
                        help="文件互传目录（默认自动选非系统盘专用文件夹 D:\\FreeRemoteFiles）")
    parser.add_argument("--token", default="", help="访问口令（默认读 token.txt，无则自动生成永久口令）")
    parser.add_argument("--once", action="store_true",
                        help="一次性口令模式：生成 10 分钟有效的临时口令/链接，过期自动作废（不写盘）")
    parser.add_argument("--renew-token", nargs="?", const="0", default=None,
                        help="更换新口令并写入 token.txt（默认永久有效；--renew-token 30 = 30 天有效）")
    parser.add_argument("--no-auth", action="store_true", help="关闭口令校验（仅限可信局域网）")
    parser.add_argument("--allow-ips", default="",
                        help="IP 白名单，逗号分隔（默认允许所有 IP，需配合口令）")
    parser.add_argument("--tls-cert", default="", help="TLS 证书文件路径（可选，启用 HTTPS）")
    parser.add_argument("--tls-key", default="", help="TLS 私钥文件路径（可选，启用 HTTPS）")
    parser.add_argument("--relay", default="",
                        help="识别码模式：中继服务器地址，如 ws://x.com:9090 或 wss://x.com")
    parser.add_argument("--id", default="", help="识别码模式：设备识别码（默认随机 6 位数字）")
    parser.add_argument("--password", default="", help="识别码模式：设备口令（默认随机生成）")
    parser.add_argument("--cookie", default="",
                        help="识别码模式：连接时携带的会话 Cookie（中继网关如需登录会话时使用，\n"
                             "可在浏览器开发者工具里复制 Cookie 填这里）")
    args = parser.parse_args()

    if args.renew_token is not None:
        # 更换口令并写入 token.txt：默认永久有效；--renew-token 30 = 30 天；0 = 永久
        try:
            days = int(args.renew_token or "0")
        except ValueError:
            days = 0
        new_tok = gen_token()
        expire = int(time.time()) + days * 86400 if days > 0 else None
        save_token_file(new_tok, expire)
        out("=" * 58)
        out(" 已生成新口令（{}）：".format(f"{days} 天有效" if days else "永久有效"))
        out(f"   访问口令 : {new_tok}")
        if expire:
            out(f"   有效期至 : {time.strftime('%Y-%m-%d %H:%M', time.localtime(expire))}")
        out("   ⚠ 旧口令已作废，请用下面的新链接更新手机书签")
        primary, ips = lan_ips()
        out(f"   手机访问 : http://{primary}:{args.port}/?token={new_tok}")
        ts = tailscale_ips()
        if ts:
            out(f"   手机访问(Tailscale跨网) : http://{ts[0]}:{args.port}/?token={new_tok}")
        out("=" * 58)
        sys.exit(0)

    token_expire = None
    if not args.token:
        # 固定口令优先：读 token.txt（新格式 = 永久有效；兼容旧格式，自动归一化）
        args.token, token_expire = load_token_file()
        if args.token:
            if token_expire:
                left = int(token_expire - time.time())
                log("INFO", f"已使用固定口令（token.txt）：{args.token}"
                            f"（有效期至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(token_expire))}，剩 {left // 86400} 天）")
            else:
                log("INFO", f"已使用固定口令（token.txt）：{args.token}（永久有效）")
    if not args.token:
        # 没有口令文件：生成直观口令并保存（永久有效，重启/换网都不变）
        args.token = gen_token()
        save_token_file(args.token, None)
        log("INFO", f"已生成新口令（token.txt，永久有效）：{args.token}")
    args.allow_ips = set(x.strip() for x in args.allow_ips.split(",") if x.strip())

    # 注入引擎参数（FAILSAFE/PAUSE=0）已由 free_remote.injection 导入时统一设置

    if args.relay:
        # ---- 识别码模式：跨网络，经中继服务器 ----
        if not args.id:
            args.id = random_id()
        if not args.password:
            args.password = gen_token()
        sys.exit(asyncio.run(relay_client(args)) or 0)  # noqa
        return

    app = make_app(args)
    app["token_expire"] = token_expire
    if args.once:
        once_tok = gen_token()
        app["once"] = {"tok": once_tok, "exp": time.time() + 600}
    out("=" * 58)
    out(" FreeRemote 已启动 —— 手机浏览器远程控制电脑")
    out(f"   本机预览 : http://127.0.0.1:{args.port}/?token={args.token}")
    primary, ips = lan_ips()
    if len(ips) == 1:
        out(f"   手机访问 : http://{ips[0]}:{args.port}/?token={args.token}")
    else:
        out(f"   手机访问 : http://{primary}:{args.port}/?token={args.token}   ← 最可能")
        for ip in ips[1:]:
            if ip == "127.0.0.1":
                continue
            out(f"       其他  : http://{ip}:{args.port}/?token={args.token}")
        if "127.0.0.1" in ips and len(ips) > 2:
            out(f"       其他  : http://127.0.0.1:{args.port}/?token={args.token}（仅本机可用）")
    ts = tailscale_ips()
    if ts:
        out(f"   手机访问(Tailscale跨网) : http://{ts[0]}:{args.port}/?token={args.token}   ← 不同网络用这个")
    if args.token and not args.no_auth:
        if token_expire:
            left = int(token_expire - time.time())
            out(f"   访问口令 : {args.token}（有效期至 "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(token_expire))}，剩 {left // 86400} 天）")
        else:
            out(f"   访问口令 : {args.token}（永久有效，保存在 token.txt）")
        out("   变更口令 : 双击 renew-token.bat（换新口令后手机书签同步更新）")
    if args.once:
        out("  ────────────────────────────────────────────────")
        out("   一次性口令（10 分钟有效，过期自动作废，不写盘）：")
        out(f"      {once_tok}")
        out(f"   一次性链接 : http://{primary}:{args.port}/?token={once_tok}")
        if ts:
            out(f"      (跨网) : http://{ts[0]}:{args.port}/?token={once_tok}")
        out("  ────────────────────────────────────────────────")
    if IS_WIN:
        out("   提示     : 首次运行若弹防火墙提示请点「允许访问」")
    out(f"   文件互传目录 : {args.share_dir}")
    if args.no_auth:
        out("   ⚠ 已关闭口令校验，请勿暴露到公网")
    if args.allow_ips:
        out(f"   IP白名单 : {', '.join(sorted(args.allow_ips))}（其余 IP 一律拒绝）")
    if args.tls_cert and args.tls_key:
        out("   TLS      : 已启用 HTTPS（手机访问请用 https://）")
    out(f"   注入引擎 : {'Windows SendInput（直通）' if IS_WIN_ENGINE else 'pyautogui（回退）'}")
    out(f"   日志     : {LOG_FILE.relative_to(BASE_DIR)}（UTF-8，错误行含 [ERROR]，可 grep）")
    out("   停止     : 按 Ctrl+C")
    out("=" * 58)
    ssl_ctx = None
    if args.tls_cert and args.tls_key:
        import ssl
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(args.tls_cert, args.tls_key)
    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)
    except OSError as e:
        # 端口被占用等绑定失败：给出可操作指引，以退出码 3 退出
        # （看门狗约定：退出码 3 = 明确要求退出，不重启，避免崩溃循环）
        if e.errno == errno.EADDRINUSE:
            out("=" * 58)
            out(f" ⚠ 端口 {args.port} 已被占用，无法启动（通常已有 FreeRemote 在运行）")
            pid = _port_pid(args.port)
            if pid:
                out(f"   占用进程 PID : {pid}")
            out("   处理方式（任选其一）：")
            out("     1. 运行 stop.bat 结束已有实例后重试")
            out(f"     2. 换端口启动：python server.py --port {args.port + 1}")
            out("=" * 58)
            sys.exit(3)
        raise


def _port_pid(port):
    """尽力查询占用端口的进程 PID（Windows netstat；失败返回 None）。"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line.upper():
                return line.split()[-1]
    except Exception:
        pass
    return None


if __name__ == "__main__":
    main()

