#!/usr/bin/env bash
# macOS / Linux 启动脚本
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "[1/2] 首次运行，创建虚拟环境并安装依赖..."
  python3 -m venv .venv
fi
if [ ! -f .venv/.deps_ok ]; then
  echo "[1/2] 安装依赖..."
  .venv/bin/python -m pip install -q -r requirements.txt
  touch .venv/.deps_ok
fi
echo "[2/2] 启动 FreeRemote..."
exec .venv/bin/python server.py "$@"
