#!/usr/bin/env bash
# macOS / Linux 自检脚本
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "尚未安装依赖，先运行 ./run.sh 完成首次安装。"
  exit 1
fi
exec .venv/bin/python doctor.py "$@"
