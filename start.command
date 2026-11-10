#!/bin/bash
# macOS 双击启动
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY=$(command -v python3)
fi
if [ -z "$PY" ]; then
    echo "错误：找不到 python3"
    echo "请先安装 Python 3.8+（https://www.python.org/downloads/）"
    read -n 1 -s -r -p "按任意键关闭"
    exit 1
fi

echo "SQLWeb 正在启动，浏览器打开 http://127.0.0.1:8899"
"$PY" sqlweb.py "$@"
echo
read -n 1 -s -r -p "服务已退出，按任意键关闭窗口"
