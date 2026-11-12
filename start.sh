#!/bin/sh
# sqlweb_offline_common 启动脚本（Linux / 麒麟 / macOS 通用）
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1

# 驱动已随包放在 vendor/，任何 3.7+ 解释器都能加载，不再依赖 venv 里装过什么。
PY=${PYTHON:-}
if [ -z "$PY" ]; then
    if [ -x "$DIR/.venv/bin/python" ]; then
        vv=$("$DIR/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || vv=""
        vmaj=$(echo "${vv:-0.0}" | cut -d. -f1); vmin=$(echo "${vv:-0.0}" | cut -d. -f2)
        if [ "$vmaj" = "3" ] && [ "$vmin" -ge 7 ]; then
            PY="$DIR/.venv/bin/python"
        fi
    fi
    if [ -z "$PY" ]; then
        for c in python3 python; do
            if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
        done
    fi
fi
if [ -z "$PY" ] || { ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; }; then
    echo "错误：找不到 Python，请先安装 Python 3.7 及以上版本"
    exit 1
fi

exec "$PY" sqlweb.py "$@"
