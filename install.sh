#!/bin/sh
# sqlweb_offline_common 安装 / 一键部署（Linux、麒麟 V10、macOS）
#
# 用法: ./install.sh [选项]
#   （不带参数）      一键部署：检查环境 -> 就绪驱动 -> 生成配置 -> 后台启动并打印访问地址
#   --foreground      前台启动（Ctrl+C 退出）
#   --no-start        只做环境准备，不启动服务
#   --check           只检查环境（不安装、不启动、不改配置）
#   --port N          设置监听端口（写入 config.json）
#   --host H          设置监听地址（默认沿用 config.json，示例为 0.0.0.0）
#   --install-deps    兼容旧参数：就绪驱动（不带参数时也默认执行这一步）
#   -h, --help        显示本帮助
#
# 驱动说明：驱动来自包内 vendor/（纯 Python 包的解包版），不需要 pip、不需要 venv、
#           不需要外网。Python 3.7 及以上都能运行；3.7 下 PostgreSQL / MySQL /
#           达梦 DM8 / SQLite 可用，SQL Server、Oracle 需 Python 3.8+ 或系统预装驱动。
#
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1

MODE="auto"; PORT=""; HOSTBIND=""

while [ $# -gt 0 ]; do
    case "$1" in
        --foreground)   MODE="fg"; shift ;;
        --background)   MODE="bg"; shift ;;
        --no-start)     MODE="nostart"; shift ;;
        --check)        MODE="check"; shift ;;
        --install-deps) shift ;;
        --port)         PORT="${2:-}"; shift 2 ;;
        --host)         HOSTBIND="${2:-}"; shift 2 ;;
        -h|--help)      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数: $1（用 --help 查看用法）"; exit 1 ;;
    esac
done

echo "================================================"
echo " sqlweb_offline_common 部署"
echo "================================================"
echo "目录: $DIR"
echo "系统: $(uname -s 2>/dev/null || echo unknown) $(uname -m 2>/dev/null || echo '')"
if [ -r /etc/os-release ]; then
    OSNAME=$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-${NAME:-}}")
    [ -n "${OSNAME:-}" ] && echo "发行版: $OSNAME"
fi

# ---- 1. 找 Python（3.7+ 即可，优先高版本）----
PY=""; PYVER=""; PYMAJ=0; PYMIN=0
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v=$("$c" -c 'import sys; print("%d %d %d" % sys.version_info[:3])' 2>/dev/null) || continue
    a=$(echo "$v" | cut -d' ' -f1); b=$(echo "$v" | cut -d' ' -f2); d=$(echo "$v" | cut -d' ' -f3)
    [ "${a:-0}" = "3" ] || continue
    if [ "$b" -ge 7 ] && [ "$b" -le 13 ]; then
        PY="$c"; PYMAJ="$a"; PYMIN="$b"; PYVER="$a.$b.$d"; break
    fi
done

if [ -z "$PY" ]; then
    echo
    echo "[FAIL] 找不到可用的 python3（需要 3.7 及以上）"
    echo "       现场机器一般自带 python3："
    echo "         麒麟 / CentOS / 统信: python3 --version"
    echo "       若版本低于 3.7，需要先用系统包管理器升级 python3"
    exit 1
fi
echo "Python: $PYVER  ($PY)"
echo "  [ok] 版本满足要求（>=3.7）"
if [ "$PYMIN" -eq 7 ]; then
    echo "  注: 当前为 Python 3.7 —— PostgreSQL / MySQL / 达梦 / SQLite 可用；"
    echo "      SQL Server、Oracle 需要 Python 3.8+ 或系统预装对应驱动。"
fi

# ---- 2. 文件完整性 ----
for f in sqlweb.py store.py drivers.py auth.py captcha.py redis_panel.py index.html login.html; do
    if [ ! -f "$f" ]; then echo "  [FAIL] 缺少文件 $f"; exit 1; fi
done
echo "  [ok] 程序文件齐全"

# ---- 3. 运行时解释器：优先 .venv，但版本必须合规 ----
RUNTIME="$PY"
if [ -x "$DIR/.venv/bin/python" ]; then
    vv=$("$DIR/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || vv=""
    if [ -n "$vv" ]; then
        vmaj=$(echo "$vv" | cut -d. -f1); vmin=$(echo "$vv" | cut -d. -f2)
        if [ "$vmaj" = "3" ] && [ "$vmin" -ge 7 ]; then
            RUNTIME="$DIR/.venv/bin/python"
        else
            echo "  [warn] .venv 里的 Python 是 $vv，不满足 >=3.7，改为使用系统 $PY"
        fi
    else
        echo "  [warn] .venv 不可用（可能创建到一半失败了），改为使用系统 $PY"
    fi
fi

# ---- 4. 驱动：vendor/ 就绪（不用 pip、不用 venv、不用外网）----
VENDOR_OK=0
if [ -d vendor ] && [ -d vendor/pg8000 ] && [ -d vendor/dmpy ]; then
    VENDOR_OK=1
fi

if [ "$VENDOR_OK" = "0" ] && [ -d wheels ]; then
    echo
    echo "驱动目录 vendor/ 不完整，正在从 wheels/ 就地解包（无需 pip、无需外网）..."
    "$PY" - "$DIR/vendor" "$DIR/wheels" <<'PYEOF'
import os, sys, zipfile

out, whl_dir = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
n_pkg = 0
for name in sorted(os.listdir(whl_dir)):
    if not name.endswith(".whl"):
        continue
    path = os.path.join(whl_dir, name)
    try:
        with zipfile.ZipFile(path) as z:
            for item in z.namelist():
                if item.endswith("/") or "/data/" in item:
                    continue
                dst = os.path.join(out, item)
                d = os.path.dirname(dst)
                if d:
                    os.makedirs(d, exist_ok=True)
                with z.open(item) as src, open(dst, "wb") as f:
                    f.write(src.read())
        n_pkg += 1
    except Exception as e:
        print("  [warn] %s 解包失败: %s" % (name, e))
print("  [ok] 已解包 %d 个 wheel 到 vendor/" % n_pkg)
PYEOF
fi

echo
echo "驱动可用性（未就绪的库在界面上会提示，不影响其它库）:"
"$RUNTIME" -c "
import sys
sys.path.insert(0, '.')
import drivers
for d in drivers.list_drivers():
    print('  %-11s %-17s %s' % (d['name'], d['label'], d['info']))
" 2>/dev/null || echo "  [warn] 驱动探测失败（不影响启动，稍后看服务日志）"

# ---- 5. 配置 ----
if [ "$MODE" = "check" ]; then
    echo
    if [ -f config.json ]; then
        echo "配置文件: 已有 config.json"
    else
        echo "配置文件: 尚无 config.json（首次部署会自动由 config.example.json 生成）"
    fi
    echo
    echo "环境检查完成（未安装、未启动、未改配置）"
    exit 0
fi

echo
if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "  [ok] 已由 config.example.json 生成 config.json"
    echo "       数据源为空，登录后在界面「数据源」里新增（也可直接编辑该文件）"
else
    echo "  [ok] 已有 config.json，保持不变"
fi
chmod 600 config.json 2>/dev/null || true
chmod 600 sqlweb.db 2>/dev/null || true

if [ -n "$PORT" ] || [ -n "$HOSTBIND" ]; then
    "$PY" - "$PORT" "$HOSTBIND" <<'PYEOF'
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

if [ "$MODE" = "nostart" ]; then
    echo
    echo "准备完成。启动命令: ./start.sh   或   $RUNTIME sqlweb.py"
    exit 0
fi

# ---- 6. 已在运行就不重复起 ----
PORT_NOW=$("$PY" -c "import json;print(json.load(open('config.json')).get('port',8899))" 2>/dev/null || echo 8899)
HOST_NOW=$("$PY" -c "import json;print(json.load(open('config.json')).get('host','0.0.0.0'))" 2>/dev/null || echo 0.0.0.0)
if [ -f .sqlweb.pid ]; then
    OLDPID=$(cat .sqlweb.pid 2>/dev/null || echo "")
    if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
        echo
        echo "服务已在运行（PID $OLDPID，$HOST_NOW:$PORT_NOW），不重复启动。"
        echo "  重启: ./stop.sh && ./install.sh     停止: ./stop.sh"
        exit 0
    fi
    rm -f .sqlweb.pid
fi

show_ip() {
    ip=$( (hostname -I 2>/dev/null || true) | awk '{print $1}' )
    [ -n "${ip:-}" ] || ip=$( (ip -4 addr 2>/dev/null | grep -m1 'inet ' | awk '{print $2}' | cut -d/ -f1) )
    [ -n "${ip:-}" ] || ip="<本机IP>"
    echo "$ip"
}

if [ "$MODE" = "fg" ]; then
    echo
    echo "================================================"
    echo "前台启动（Ctrl+C 退出）"
    echo "================================================"
    exec "$RUNTIME" sqlweb.py
fi

# ---- 7. 后台启动 ----
echo
if [ -f sqlweb.log ]; then
    mv -f sqlweb.log sqlweb.log.prev 2>/dev/null || rm -f sqlweb.log
fi
nohup "$RUNTIME" sqlweb.py > sqlweb.log 2>&1 &
echo $! > .sqlweb.pid
sleep 2

PID=$(cat .sqlweb.pid 2>/dev/null || echo "")
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "[FAIL] 启动失败，最近日志："
    sed -n '1,60p' sqlweb.log 2>/dev/null
    rm -f .sqlweb.pid
    exit 1
fi

IP=$(show_ip)
PORT_NOW=$("$PY" -c "import json;print(json.load(open('config.json')).get('port',8899))" 2>/dev/null || echo 8899)

echo "================================================"
echo " 已启动（PID $PID）"
echo "================================================"
echo " 访问地址: http://$IP:$PORT_NOW/login"
echo " 本机访问: http://127.0.0.1:$PORT_NOW/login"
echo " 日志:     $DIR/sqlweb.log"
echo " 停止:     ./stop.sh      重启: ./stop.sh && ./install.sh"
echo

TOKEN=$(grep -A1 "初始化令牌" sqlweb.log 2>/dev/null | grep -E '^[[:space:]]*[0-9]{4,}[[:space:]]*$' | head -1 | tr -d ' \r')
if [ -n "${TOKEN:-}" ]; then
    echo "================================================"
    echo " 首次使用：打开上面的地址，填管理员账号口令"
    echo " 初始化令牌: $TOKEN   （仅本次启动有效）"
    echo "================================================"
else
    sed -n '1,40p' sqlweb.log | grep -E "监听|数据源" || true
fi
