#!/bin/sh
# sqlweb_offline_common 启动脚本（Linux / macOS 通用）
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1

# 优先用 install.sh 创建的虚拟环境，其次用系统 python3
PY=${PYTHON:-}
if [ -z "$PY" ]; then
    if [ -x "$DIR/.venv/bin/python" ]; then
        PY="$DIR/.venv/bin/python"
    else
        PY=python3
    fi
fi
if ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; then
    echo "错误：找不到 Python，请先安装 Python 3.8 及以上版本"
    exit 1
fi

exec "$PY" sqlweb.py "$@"
