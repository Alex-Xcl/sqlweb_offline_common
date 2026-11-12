# -*- coding: utf-8 -*-
"""离线改配置：端口 / base_path / 只读默认可直接改状态库，不用记 SQL。

用法：
    python3 setconfig.py                              # 列出当前配置（含生效来源）
    python3 setconfig.py --get port                    # 取单个值，脚本里好用
    python3 setconfig.py --set base_path=/sqlweb       # 改子路径（nginx 反代 /sqlweb/）
    python3 setconfig.py --set port=8080 --set base_path=/
    python3 setconfig.py --set readonly=true
    python3 setconfig.py --db /opt/xxx/sqlweb.db --set base_path=/sqlweb
    python3 setconfig.py --json config.json --set port=8899    # 改还没导入的 JSON 种子

说明：
    1.3.0 起配置真源是 sqlweb.db（SQLite）。同目录有 sqlweb.db 时默认改它；
    还没有状态库（首次部署）才落到 config.json 种子上。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "sqlweb.db")
JSON_PATH = os.path.join(HERE, "config.json")

INT_KEYS = ("port", "max_rows", "connect_timeout", "statement_timeout_ms", "redis_scan_count")
BOOL_KEYS = ("readonly",)


def norm_base(value):
    v = (value or "").strip()
    if v in ("", "/"):
        return ""
    if not v.startswith("/"):
        v = "/" + v
    return v.rstrip("/")


def coerce(key, value):
    if key == "base_path":
        return norm_base(value)
    if key in INT_KEYS:
        try:
            n = int(str(value).strip())
        except Exception:
            raise SystemExit("值不合法：%s=%r 需要整数" % (key, value))
        if key == "port" and not (1 <= n <= 65535):
            raise SystemExit("端口不合法：%d（要求 1-65535）" % n)
        return n
    if key in BOOL_KEYS:
        return str(value).strip().lower() in ("1", "true", "yes", "on", "y")
    return value


def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return raw
    return {}


def save_json(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)


def pick_target(args):
    """返回 (kind, path)。"""
    if args.db:
        return "db", args.db
    if args.json:
        return "json", args.json
    if os.path.exists(DB_PATH):
        return "db", DB_PATH
    if os.path.exists(JSON_PATH):
        return "json", JSON_PATH
    raise SystemExit("既没有 sqlweb.db 也没有 config.json：请先跑 ./install.sh，或指定 --db / --json")


def main():
    ap = argparse.ArgumentParser(description="sqlweb 配置读写（SQLite 状态库优先）")
    ap.add_argument("--db", default=None, help="指定状态库路径（默认同目录 sqlweb.db）")
    ap.add_argument("--json", default=None, help="显式改 JSON 种子（默认只在没有状态库时用）")
    ap.add_argument("--set", action="append", default=[], metavar="K=V", help="设置一项，可重复")
    ap.add_argument("--get", default=None, metavar="K", help="只取值（不带 --set 时生效）")
    args = ap.parse_args()

    kind, path = pick_target(args)

    if kind == "db":
        sys.path.insert(0, HERE)
        import store as state_store
        st = state_store.SqliteStore(path)
        cfg = st.load_config()
    else:
        st = None
        cfg = load_json(path)

    if args.set:
        changed = []
        for item in args.set:
            if "=" not in item:
                raise SystemExit("参数格式应为 K=V：%r" % item)
            k, v = item.split("=", 1)
            k = k.strip()
            if not k:
                raise SystemExit("参数名不能为空：%r" % item)
            if k.startswith("_"):
                raise SystemExit("不能改运行时键：%s" % k)
            cfg[k] = coerce(k, v)
            changed.append("%s=%r" % (k, cfg[k]))
        if kind == "db":
            st.save_config(cfg)
        else:
            save_json(path, cfg)
        print("已写入 %s（%s）：" % (path, "SQLite 状态库" if kind == "db" else "JSON 种子"))
        for c in changed:
            print("   " + c)
        if cfg.get("base_path"):
            print("访问路径: http://<IP>:%s%s/" % (cfg.get("port", 8899), cfg["base_path"]))
        else:
            print("访问路径: http://<IP>:%s/" % cfg.get("port", 8899))
        print("改完要重启服务才生效：./stop.sh && ./install.sh    （或 sudo systemctl restart sqlweb-offline）")
        return 0

    if args.get:
        v = cfg.get(args.get)
        if isinstance(v, bool):
            print("true" if v else "false")
        elif v is None:
            print("")
        else:
            print(v)
        return 0

    # 无参数：列出当前配置
    print("生效来源: %s（%s）" % (path, "SQLite 状态库" if kind == "db" else "JSON 种子"))
    show = ["title", "host", "port", "base_path", "readonly", "max_rows", "default_db",
            "connect_timeout", "statement_timeout_ms", "redis_scan_count"]
    for k in show:
        print("  %-22s %s" % (k, cfg.get(k)))
    print("  %-22s %d 个" % ("databases", len(cfg.get("databases") or [])))
    print("  %-22s %d 个" % ("redis 实例", len(cfg.get("redis") or [])))
    bp = cfg.get("base_path") or ""
    print("访问路径: http://<IP>:%s%s/" % (cfg.get("port", 8899), bp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
