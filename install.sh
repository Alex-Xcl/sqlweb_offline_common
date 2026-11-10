#!/bin/sh
# sqlweb_offline_common 安装/启动脚本（Linux、macOS）
#
# 用法: ./install.sh [选项]
#   --install-deps   创建 .venv 并离线安装 wheels/ 内的纯 Python 驱动
#   --port N         设置监听端口
#   --host H         设置监听地址（默认 0.0.0.0）
#   --background     后台启动，日志写 sqlweb.log，PID 写 .sqlweb.pid
#   --no-start       只做环境准备，不启动服务
#   --check          只检查环境，不安装不启动
#   -h, --help       显示本帮助
#
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1

INSTALL_DEPS=0; BACKGROUND=0; NO_START=0; CHECK_ONLY=0; PORT=""; HOST=""

while [ $# -gt 0 ]; do
    case "$1" in
        --install-deps) INSTALL_DEPS=1; shift ;;
        --background)   BACKGROUND=1; shift ;;
        --no-start)     NO_START=1; shift ;;
        --check)        CHECK_ONLY=1; shift ;;
        --port)         PORT="${2:-}"; shift 2 ;;
        --host)         HOST="${2:-}"; shift 2 ;;
        -h|--help)      sed -n '2,14p' "$0" | sed 's/^# //; s/^#//'; exit 0 ;;
        *) echo "未知参数: $1（用 --help 查看用法）"; exit 1 ;;
    esac
done

echo "================================================"
echo " sqlweb_offline_common 安装"
echo "================================================"
echo "目录: $DIR"
echo "系统: $(uname -s 2>/dev/null || echo unknown) $(uname -m 2>/dev/null || echo '')"

# ---- 1. 找 Python ----
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo
    echo "[FAIL] 找不到 python3 / python"
    echo "       请先安装 Python 3.8 及以上版本"
    exit 1
fi

PYVER=$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
PYOK=$("$PY" -c 'import sys; print(1 if sys.version_info[:2] >= (3, 8) else 0)')
echo "Python: $PYVER  ($PY)"
if [ "$PYOK" != "1" ]; then
    echo
    echo "[FAIL] 需要 Python 3.8 及以上，当前 $PYVER"
    echo "       离线环境请自备对应平台的 Python 安装包"
    exit 1
fi
echo "  [ok] Python 版本满足要求 (>=3.8)"

# ---- 2. 文件完整性 ----
for f in sqlweb.py drivers.py auth.py captcha.py redis_panel.py index.html login.html; do
    if [ ! -f "$f" ]; then echo "  [FAIL] 缺少文件 $f"; exit 1; fi
done
echo "  [ok] 程序文件齐全"

# ---- 3. 驱动探测 ----
echo
echo "驱动可用性（未装驱动的库在界面上会提示缺失，不影响其它库）:"
"$PY" -c "
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-12s %-16s %s' % (d['name'], d['label'], d['info']))
" 2>/dev/null || echo "  [warn] 驱动探测失败（不影响启动，稍后看服务日志）"

# ---- 4. 离线装驱动 ----
if [ "$INSTALL_DEPS" = "1" ]; then
    echo
    echo "离线安装 wheels/ 内的驱动（PyMySQL、pg8000、dm-pure）..."
    if [ ! -d wheels ]; then
        echo "  [FAIL] 没有 wheels/ 目录"; exit 1
    fi
    # --system-site-packages：继承系统 Python 已装的驱动，避免 .venv 成为空环境后
    # start.sh 切过去把 psycopg2 / pymssql / oracledb 等一并"弄丢"
    if [ ! -d .venv ]; then
        "$PY" -m venv --system-site-packages .venv || { echo "  [FAIL] 创建 .venv 失败（某些精简系统缺 venv 模块）"; exit 1; }
    fi
    ./.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || true
    ./.venv/bin/python -m pip install --no-index --find-links=wheels PyMySQL pg8000 dm-pure
    if [ $? -ne 0 ]; then
        echo "  [FAIL] 离线安装失败"; exit 1
    fi
    echo "  [ok] 已装入 .venv"
    echo
    echo "安装后驱动可用性:"
    ./.venv/bin/python -c "
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-12s %-16s %s' % (d['name'], d['label'], d['info']))
"
fi

# ---- 5. 配置 ----
echo
if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "  [ok] 已由 config.example.json 生成 config.json"
    echo "       config.json 里是示例数据源，启动后在界面「数据源」里改，或直接编辑该文件"
else
    echo "  [ok] 已有 config.json，保持不变"
fi
chmod 600 config.json 2>/dev/null || true

if [ -n "$PORT" ] || [ -n "$HOST" ]; then
    "$PY" - "$PORT" "$HOST" <<'PYEOF'
import json, sys, os
port, host = sys.argv[1], sys.argv[2]
p = "config.json"
with open(p, encoding="utf-8") as f:
    cfg = json.load(f)
if port:
    cfg["port"] = int(port)
if host:
    cfg["host"] = host
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.replace(tmp, p)
print("  [ok] 已更新监听地址为 %s:%s" % (cfg.get("host"), cfg.get("port")))
PYEOF
fi

if [ "$CHECK_ONLY" = "1" ]; then
    echo
    echo "环境检查完成（未启动服务）"
    exit 0
fi

# ---- 6. 决定运行时 Python ----
if [ -x "$DIR/.venv/bin/python" ]; then
    RUNTIME="$DIR/.venv/bin/python"
else
    RUNTIME="$PY"
fi

if [ "$NO_START" = "1" ]; then
    echo
    echo "准备完成。启动命令: ./start.sh   或   $RUNTIME sqlweb.py"
    exit 0
fi

# ---- 7. 启动 ----
echo
echo "================================================"
if [ "$BACKGROUND" = "1" ]; then
    nohup "$RUNTIME" sqlweb.py > sqlweb.log 2>&1 &
    echo $! > .sqlweb.pid
    sleep 2
    echo "已在后台启动，PID $(cat .sqlweb.pid)"
    echo "日志: $DIR/sqlweb.log"
    echo
    sed -n '1,40p' sqlweb.log | grep -E "初始化令牌|监听|数据源" || true
    echo
    echo "停止: ./stop.sh      查看日志: tail -f sqlweb.log"
else
    echo "前台启动（Ctrl+C 退出）"
    echo "================================================"
    exec "$RUNTIME" sqlweb.py
fi
