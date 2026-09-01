#!/usr/bin/env bash
# 停止所有 FreeRemote 进程（relay.py / server.py）
echo "正在停止所有 FreeRemote 进程..."
pids=$(ps -eo pid,command | grep -E "relay\.py|server\.py" | grep -v grep | awk '{print $1}')
if [ -n "$pids" ]; then
  kill $pids 2>/dev/null
  sleep 1
  kill -9 $pids 2>/dev/null
  echo "已停止: $pids"
else
  echo "没有运行中的 FreeRemote 进程。"
fi
