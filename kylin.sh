#!/bin/sh
# sqlweb_offline_common —— 麒麟 / 信创 Linux 一键入口
#
# 用法:
#   ./kylin.sh                 一键部署并后台启动（等价 ./install.sh）
#   ./kylin.sh check           只检查环境
#   ./kylin.sh service install     装成 systemd 服务（开机自启，需 root）
#   ./kylin.sh service uninstall   卸载 systemd 服务
#   ./kylin.sh service status      查看 systemd 服务状态
#
# 说明：不需要 pip、不需要 venv、不需要外网；Python 3.7 即可运行。
#       ./kylin.sh 与 ./install.sh 都会自动跳过缺失权限位的问题（用 sh 显式调用）。
#
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
UNIT="/etc/systemd/system/sqlweb-offline.service"

need_root() {
    if [ "$(id -u)" != "0" ]; then
        echo "[FAIL] 该操作需要 root 权限，请用 sudo 执行"
        exit 1
    fi
}

need_systemd() {
    # 容器环境里 systemctl 命令存在但 systemd 没跑，直接调会全部报 "System has not been booted"，
    # 却仍然打印成功 —— 所以先按标准标志判断，再往下走。
    if [ ! -d /run/systemd/system ]; then
        echo "[FAIL] 本机 systemd 未运行（容器环境或非 systemd 发行版），无法管理开机服务"
        echo "       请改用: ./install.sh 后台启动 / ./stop.sh 停止"
        exit 1
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "[FAIL] 本机没有 systemctl，无法注册服务；请直接用 ./install.sh 后台运行"
        exit 1
    fi
}

is_kylin() {
    if [ -r /etc/os-release ]; then
        . /etc/os-release 2>/dev/null || true
        case "${ID:-}${NAME:-}" in
            *kylin*|*Kylin*|*麒麟*) return 0 ;;
        esac
    fi
    return 1
}

do_service() {
    sub="${1:-}"
    case "$sub" in
        install)
            need_root
            need_systemd
            if [ ! -f config.json ]; then
                echo "[FAIL] 还没初始化，请先执行一次 ./kylin.sh"
                exit 1
            fi
            cat > "$UNIT" <<EOF
[Unit]
Description=sqlweb_offline_common 离线 SQL 控制台
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=/bin/sh $DIR/start.sh
Restart=on-failure
RestartSec=5
StandardOutput=append:$DIR/sqlweb.log
StandardError=append:$DIR/sqlweb.log

[Install]
WantedBy=multi-user.target
EOF
            systemctl daemon-reload || {
                echo "[FAIL] systemctl daemon-reload 失败"; exit 1
            }
            systemctl enable sqlweb-offline >/dev/null 2>&1 || {
                echo "[FAIL] 设置开机自启失败"; exit 1
            }
            # 已在跑的 PID 进程先停掉，避免端口占用
            if [ -f .sqlweb.pid ]; then
                oldpid=$(cat .sqlweb.pid 2>/dev/null || echo "")
                if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
                    kill "$oldpid" 2>/dev/null; sleep 1
                fi
                rm -f .sqlweb.pid
            fi
            systemctl restart sqlweb-offline || true
            sleep 2
            if ! systemctl is-active --quiet sqlweb-offline; then
                echo "[FAIL] 服务未能启动，排查: journalctl -u sqlweb-offline -n 50"
                echo "       常见原因：端口被占、config.json 内容有误"
                exit 1
            fi
            echo "[ok] 已注册并启动: sqlweb-offline.service（已设为开机自启）"
            systemctl --no-pager --lines=0 status sqlweb-offline || true
            echo "  日志: journalctl -u sqlweb-offline -f   或  $DIR/sqlweb.log"
            ;;
        uninstall)
            need_root
            need_systemd
            systemctl stop sqlweb-offline 2>/dev/null || true
            systemctl disable sqlweb-offline 2>/dev/null || true
            rm -f "$UNIT"
            systemctl daemon-reload 2>/dev/null || true
            echo "[ok] 已卸载 systemd 服务（程序文件与配置保留）"
            ;;
        status)
            need_systemd
            systemctl --no-pager status sqlweb-offline 2>/dev/null || echo "服务未注册"
            ;;
        *)
            echo "用法: ./kylin.sh service install|uninstall|status"
            exit 1
            ;;
    esac
}

case "${1:-}" in
    service) shift; do_service "${1:-}" ;;
    check)   shift; exec sh "$DIR/install.sh" --check "$@" ;;
    *)
        if is_kylin; then
            echo "检测到麒麟系统，按信创环境流程部署。"
        fi
        exec sh "$DIR/install.sh" "$@"
        ;;
esac
