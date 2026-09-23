#!/usr/bin/env bash
# 停止所有 FreeRemote 进程
#   - 先杀看门狗（防止服务被自动重启"复活"）
#   - 再清扫全部服务进程（relay.py / server.py / hot.py）
# 重启：./run.sh
echo "正在停止所有 FreeRemote 进程..."
pids=$(ps -eo pid,command | grep -E "hot\.py|relay\.py|server\.py" | grep -v grep | awk '{print $1}')
if [ -n "$pids" ]; then
  kill $pids 2>/dev/null
  sleep 1
  kill -9 $pids 2>/dev/null
  echo "已停止: $pids"
else
  echo "没有运行中的 FreeRemote 进程。"
fi
rm -f .freebuff/hot.pid 2>/dev/null
