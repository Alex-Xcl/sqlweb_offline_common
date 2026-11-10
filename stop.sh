#!/bin/sh
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
PIDF=".sqlweb.pid"
if [ ! -f "$PIDF" ]; then
    echo "没有找到 $PIDF，服务可能不是用 --background 启动的"
    exit 1
fi
PID=$(cat "$PIDF")
if ! kill -0 "$PID" 2>/dev/null; then
    echo "进程 $PID 已不存在，清理 PID 文件"
    rm -f "$PIDF"
    exit 0
fi
kill "$PID" 2>/dev/null
sleep 1
if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" 2>/dev/null
    echo "已强制停止进程 $PID"
else
    echo "已停止进程 $PID"
fi
rm -f "$PIDF"
