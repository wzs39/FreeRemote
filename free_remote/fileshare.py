# -*- coding: utf-8 -*-
"""文件互传：手机 <-> 电脑 上传下载。"""
import os
import secrets
from pathlib import Path

from aiohttp import web

from .webcore import audit, auth_ok, client_ip

# ---------------------------------------------------------------- 文件互传

def default_share_dir() -> str:
    """自动选择「不在系统盘(C:)」的专用文件夹，方便找文件；没有其它盘则退回下载目录。"""
    if os.name == "nt":
        try:
            sys_letter = os.path.splitdrive(os.getcwd())[0][0].upper()
        except Exception:
            sys_letter = "C"
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            if letter == sys_letter:
                continue
            drive = f"{letter}:"
            if os.path.exists(drive + os.sep):
                p = drive + os.sep + "FreeRemoteFiles"
                try:
                    os.makedirs(p, exist_ok=True)
                    return p
                except OSError:
                    continue
    return str(Path.home() / "Downloads")


def safe_file_name(name: str) -> str:
    """去除路径分隔，只保留文件名。"""
    name = os.path.basename(name.replace("\\", "/")).strip()
    return name or "upload.bin"


def resolve_fs_path(root: str, p: str) -> str:
    """把手机端路径参数解析为电脑本地绝对路径。

    相对路径以共享目录为根（与文档契约一致，此前误按服务进程 CWD 解析，
    上传到共享根的文件用相对路径下载会 404）；绝对路径原样归一化。
    """
    if not p:
        return root
    q = p.replace("/", os.sep)
    if not os.path.isabs(q):
        q = os.path.join(root, q)
    return os.path.abspath(q)


def list_entries(path: str):
    """列出目录内容（目录在前，按名称排序）。"""
    entries = []
    for name in sorted(os.listdir(path)):
        fp = os.path.join(path, name)
        try:
            st = os.stat(fp)
            entries.append({
                "name": name,
                "is_dir": os.path.isdir(fp),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
        except OSError:
            continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


async def files_handler(request):
    """文件浏览：列出目录。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    root = args.share_dir
    path = resolve_fs_path(root, request.query.get("path", ""))
    audit(request.app, "文件浏览", client_ip(request), path)
    try:
        return web.json_response({
            "ok": True, "path": path,
            "parent": os.path.dirname(path),
            "entries": list_entries(path),
        })
    except OSError as e:
        return web.json_response({"ok": False, "error": str(e)})


async def upload_handler(request):
    """上传：手机 → 电脑，原始字节流写盘。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    root = args.share_dir
    os.makedirs(root, exist_ok=True)
    dirpath = resolve_fs_path(root, request.query.get("path", ""))
    name = safe_file_name(request.query.get("name", "upload.bin"))
    dest = os.path.join(dirpath, name)
    tmp = dest + f".{secrets.token_hex(3)}.part"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.content.iter_chunked(256 * 1024):
                f.write(chunk)
                size += len(chunk)
        os.replace(tmp, dest)
        audit(request.app, "文件上传", client_ip(request), f"{name} ({size}B → {dirpath})")
        return web.json_response({"ok": True, "name": name, "size": size, "path": dirpath})
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def download_handler(request):
    """下载：电脑 → 手机，流式发送。"""
    args = request.app["args"]
    if not auth_ok(request):
        return web.Response(status=403)
    root = args.share_dir
    path = resolve_fs_path(root, request.query.get("path", ""))
    if not os.path.isfile(path):
        return web.Response(status=404, text="file not found")
    audit(request.app, "文件下载", client_ip(request), path)
    name = os.path.basename(path)
    size = os.path.getsize(path)
    resp = web.StreamResponse(headers={
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Length": str(size),
    })
    await resp.prepare(request)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            await resp.write(chunk)
    return resp

