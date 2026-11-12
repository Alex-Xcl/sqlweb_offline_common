#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sqlweb_offline_common —— 通用型离线 SQL 网页控制台

特性：
    * 跨平台：Linux / Windows / macOS，纯标准库，无框架依赖
    * 多数据库：PostgreSQL / MySQL / SQL Server / Oracle / 达梦 DM8 / SQLite
    * 强制登录：PBKDF2 口令 + 会话 Cookie，首次启动必须初始化管理员
    * 动态图片验证码：纯标准库手写 PNG（不依赖 Pillow）
    * 界面配置数据源：测试连接 -> 保存 -> 热加载，无需重启

用法：
    python3 sqlweb.py                      # 用同目录 config.json
    python3 sqlweb.py --port 8899 --host 0.0.0.0
    python3 sqlweb.py --config /path/config.json
"""
import argparse
import base64
import json
import os
import secrets
import sys
import threading
import time
import traceback
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 随包携带的纯 Python 驱动（vendor/ 解包版）。
# 目的：目标机没有 pip、没有 venv、没有外网，甚至 Python 只有 3.7 时，
# pip 会因为 Requires-Python（pg8000/scramp/dm-pure 声明 >=3.8）直接拒装；
# 这些包本身是纯 Python 代码，解包后加到 sys.path 就能用。
VENDOR = os.path.join(HERE, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

import drivers
import captcha
import redis_panel
import store as state_store
from auth import (AuthStore, SessionManager, RateLimiter, CaptchaStore,
                  parse_cookie, SESSION_TTL)

APP_VERSION = "common-1.3.0"

CONFIG_PATH = os.path.join(HERE, "config.json")
INDEX_PATH = os.path.join(HERE, "index.html")
LOGIN_PATH = os.path.join(HERE, "login.html")
AUTH_PATH = os.path.join(HERE, "auth.json")
# 运行状态统一落在单文件 SQLite：数据源 / Redis 实例 / 账号都在 sqlweb.db 里
DB_PATH = os.path.join(HERE, "sqlweb.db")
STORE = None      # main() 里装配；为 None 时退回老的 JSON 模式（自测与兼容用）

DEFAULTS = {
    "title": "SQLWeb 离线控制台",
    "host": "0.0.0.0",
    "port": 8899,
    "base_path": "",
    "readonly": True,
    "max_rows": 1000,
    "connect_timeout": 10,
    "statement_timeout_ms": 30000,
    "default_db": "",
    "databases": [],
    "redis": [],
    "redis_scan_count": 200,
}

READONLY_HEADS = ("select", "with", "values", "explain", "show", "table", "declare", "desc")


# ---------------------------------------------------------------- 配置
def load_config(path=None):
    """读配置。

    默认读 sqlweb.db（SQLite）；显式给一个 .json 路径时按老的 JSON 文件读，
    单元自测和一次性迁移仍走这条老路。
    """
    engine = "json"
    cfg = dict(DEFAULTS)
    if path is None and STORE is not None:
        path = STORE.path
        cfg.update(STORE.load_config())
        engine = "sqlite"
    else:
        path = path or CONFIG_PATH
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                cfg.update(raw)
    cfg["_path"] = path
    cfg["_engine"] = engine
    # 数据源补齐默认字段
    dbs = []
    for i, d in enumerate(cfg.get("databases") or []):
        if not isinstance(d, dict):
            continue
        d = dict(d)
        d.setdefault("key", "ds%d" % (i + 1))
        d.setdefault("label", d["key"])
        d.setdefault("driver", "postgresql")
        d.setdefault("readonly", cfg.get("readonly", True))
        d.setdefault("port", drivers.DEFAULT_PORTS.get((d.get("driver") or "").lower(), ""))
        d.setdefault("visible_schemas", [])
        if isinstance(d.get("visible_schemas"), str):
            d["visible_schemas"] = [x.strip() for x in
                                    d["visible_schemas"].replace("，", ",").split(",") if x.strip()]
        dbs.append(d)
    cfg["databases"] = dbs
    if not cfg.get("default_db") and dbs:
        cfg["default_db"] = dbs[0]["key"]
    return cfg


def save_config(cfg, path=None):
    """把配置落盘。只落白名单字段，不把运行时键写进去。

    sqlite 模式：整份配置一个事务写进 sqlweb.db；
    json  模式：沿用临时文件 + os.replace，保证不会读到半个文件。
    """
    out = {}
    for k, v in cfg.items():
        if k.startswith("_"):
            continue
        out[k] = v
    out["databases"] = [
        {kk: vv for kk, vv in d.items() if not kk.startswith("_")}
        for d in (cfg.get("databases") or [])
    ]
    if path is None and STORE is not None and cfg.get("_engine") == "sqlite":
        return STORE.save_config(out)
    path = path or cfg.get("_path") or CONFIG_PATH
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)
    return path


def get_db(cfg, key):
    dbs = cfg.get("databases") or []
    if not dbs:
        raise RuntimeError("还没有配置任何数据源，请先在“数据源”页面添加")
    if key:
        for d in dbs:
            if d.get("key") == key:
                return d
        raise RuntimeError("数据源不存在: %s" % key)
    for d in dbs:
        if d.get("key") == cfg.get("default_db"):
            return d
    return dbs[0]


def public_ds(d):
    """给界面看的数据源字段（不含密码）。"""
    return {
        "key": d.get("key"),
        "label": d.get("label"),
        "driver": d.get("driver"),
        "host": d.get("host") or "",
        "port": d.get("port") or "",
        "user": d.get("user") or "",
        "dbname": d.get("dbname") or "",
        "path": d.get("path") or "",
        "readonly": bool(d.get("readonly", True)),
        "visible_schemas": list(d.get("visible_schemas") or []),
    }


# ---------------------------------------------------------------- 账号可见范围
def allow_dbs_of(user):
    """该账号能看的数据源 key 集合。返回 None 表示不受限（管理员）。"""
    u = AppState.auth.get(user) if AppState.auth else None
    if not u:
        return []
    if (u.get("role") or "") == "admin":
        return None
    return [str(x) for x in (u.get("allow_dbs") or [])]


def ds_allowed(user, key):
    allow = allow_dbs_of(user)
    return True if allow is None else (key in allow)


def visible_dbs(user):
    """按账号授权过滤后的数据源列表（管理员=全部）。"""
    allow = allow_dbs_of(user)
    dbs = (AppState.cfg or {}).get("databases") or []
    if allow is None:
        return list(dbs)
    return [d for d in dbs if d.get("key") in allow]


def schema_in_scope(ds, schema):
    """数据源级 visible_schemas 白名单；未配置=全部可见。"""
    white = ds.get("visible_schemas") or []
    if not white:
        return True
    return str(schema or "").strip().upper() in {str(w).strip().upper() for w in white}


def filter_schemas(ds, schemas):
    white = ds.get("visible_schemas") or []
    if not white:
        return list(schemas)
    return [s for s in schemas if schema_in_scope(ds, s)]


# ---------------------------------------------------------------- Redis 可见范围
def allow_redis_of(user):
    """该账号能看的 Redis 实例 key 集合。返回 None 表示不受限（管理员）。"""
    u = AppState.auth.get(user) if AppState.auth else None
    if not u:
        return []
    if (u.get("role") or "") == "admin":
        return None
    return [str(x) for x in (u.get("allow_redis") or [])]


def visible_redis(user):
    """按账号授权过滤后的 Redis 实例列表（管理员=全部）。"""
    allow = allow_redis_of(user)
    insts = (AppState.cfg or {}).get("_redis") or []
    if allow is None:
        return list(insts)
    return [i for i in insts if i.get("key") in allow]


def public_redis(inst):
    """对外暴露的实例信息：不含口令、不含账号。"""
    return {
        "key": inst.get("key"),
        "label": inst.get("label"),
        "host": inst.get("host"),
        "port": inst.get("port"),
        "db": inst.get("db"),
        "readonly": bool(inst.get("readonly", True)),
        "has_auth": bool(inst.get("password")),
    }


def redis_allowed(user, key):
    allow = allow_redis_of(user)
    return True if allow is None else (key in allow)


def get_redis(cfg, key):
    insts = (cfg or {}).get("_redis") or []
    if not insts:
        return None
    if not key:
        return insts[0]
    for i in insts:
        if i.get("key") == key:
            return i
    return None


# ---------------------------------------------------------------- SQL 工具
def strip_comments(sql):
    out = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            if j < -1:
                j = n
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i))
            i = j
        elif c in ("'", '"'):
            q = c
            j = i + 1
            while j < n:
                if sql[j] == q:
                    if j + 1 < n and sql[j + 1] == q:
                        j += 2
                        continue
                    break
                j += 1
            j = min(j + 1, n)
            out.append(sql[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


_END_NO_DEC = ("if", "loop", "while", "repeat", "for")
_TX_BEGIN_NEXT = ("tran", "transaction", "work")


def _next_word(s, i):
    """从 i 起跳过分隔符取下一个词（小写）；没有则返回空串。"""
    n = len(s)
    while i < n and s[i].isspace():
        i += 1
    j = i
    while j < n and (s[j].isalnum() or s[j] in "_#$"):
        j += 1
    return s[i:j].lower()


def split_statements(sql):
    """按分号切分，尊重引号、括号，以及 BEGIN…END 程序块。

    为什么必须识别程序块：达梦 / Oracle 的
    `CREATE OR REPLACE PROCEDURE ... AS BEGIN NULL; END;` 内部每句都带分号，
    按分号硬切会拆成「……AS BEGIN NULL」+「END」两条，服务端必然报语法错误，
    表现就是“这种存储过程建不了”。这里让块内分号不切分：
    BEGIN / CASE 增一层，`END;` / `END CASE` 减一层，
    `END IF` / `END LOOP` / `END WHILE` / `END FOR` / `END REPEAT` 不算收尾
    （它们配对的 IF / LOOP / WHILE / FOR / REPEAT 没计入）。
    单独的事务 `BEGIN;`（PostgreSQL / SQL Server BEGIN TRAN）不当作块。
    """
    s = strip_comments(sql)
    stmts = []
    buf = []
    depth = 0       # 括号层数
    q = None        # 当前引号
    block = 0       # BEGIN / CASE 嵌套层数
    skip_case = False   # 刚处理过 END CASE，后面那个 case 不再重复计数
    word = ""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if q:
            buf.append(c)
            if c == q:
                if i + 1 < n and s[i + 1] == q:
                    buf.append(s[i + 1])
                    i += 2
                    continue
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            word = ""
            q = c
            buf.append(c)
            i += 1
            continue
        if c.isalnum() or c in "_#$":
            word += c
            buf.append(c)
            i += 1
            continue
        if word:
            w = word.lower()
            word = ""
            if w != "case":
                skip_case = False
            if w == "case":
                if skip_case:
                    skip_case = False         # 这个 case 属于上一句 END CASE，不重复计数
                elif block > 0:
                    block += 1
            elif w == "begin":
                nxt = s[i:].lstrip()
                if nxt.startswith(";") or _next_word(s, i) in _TX_BEGIN_NEXT:
                    pass                      # 事务 BEGIN; 不进块模式
                else:
                    block += 1
            elif w == "end":
                nw = _next_word(s, i)
                if nw in _END_NO_DEC:
                    pass                      # END IF / END LOOP … 只是闭合 if / loop
                elif block > 0:
                    block -= 1
                    if nw == "case":
                        skip_case = True
        if c == "(":
            depth += 1
            buf.append(c)
        elif c == ")":
            depth = max(0, depth - 1)
            buf.append(c)
        elif c == ";" and depth == 0:
            if block > 0:
                buf.append(c)                 # 块内分号，继续累积
            else:
                t = "".join(buf).strip()
                if t and t != "/":            # 单独的 "/" 是 SQL*Plus 提交符，丢掉
                    stmts.append(t)
                buf = []
        else:
            buf.append(c)
        i += 1
    t = "".join(buf).strip()
    if t and t != "/":
        stmts.append(t)
    return stmts


def readonly_check(sql):
    """只读模式下判断语句能否执行。返回 (allowed, reason)。"""
    s = strip_comments(sql).strip()
    if not s:
        return False, "空语句"
    head = s.split(None, 1)[0].lower().strip("(")
    if head in READONLY_HEADS:
        return True, ""
    return False, ("该数据源是只读（readonly），拦截了以 %s 开头的语句。"
                   "建表 / 建存储过程 / 写入请先在【数据源管理】把它改成「可写」再执行" % head)


def jsonable(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return str(v)
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except Exception:
            return "<binary %d bytes>" % len(v)
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)


def _use_schema(conn, driver_name, schema):
    """切换当前 schema / 库。各驱动写法不同，失败不致命。"""
    if not schema:
        return
    sqls = {
        "postgresql": ["SET search_path TO %s" % drivers.quote_literal(schema)],
        "mysql": [],
        "oracle": ["ALTER SESSION SET CURRENT_SCHEMA = %s" % drivers.quote_literal(schema.upper())],
        "dameng": ["SET SCHEMA %s" % schema.upper()],
        "sqlserver": [],
        "sqlite": [],
    }.get(driver_name, [])
    for s in sqls:
        try:
            cur = conn.cursor()
            cur.execute(s)
            cur.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 查询执行
def run_query(cfg, ds, sql, max_rows, schema=None):
    started = time.time()
    driver = drivers.get_driver(ds.get("driver"))
    readonly = bool(ds.get("readonly", cfg.get("readonly", True)))
    conn = None
    try:
        conn = driver.connect(ds, timeout=cfg.get("connect_timeout", 10),
                              statement_timeout_ms=cfg.get("statement_timeout_ms", 30000))
        if schema:
            _use_schema(conn, driver.name, schema)

        stmts = split_statements(sql)
        if not stmts:
            return {"ok": False, "error": {"message": "SQL 为空", "stage": "parse"},
                    "elapsed_ms": 0}

        if readonly:
            for s in stmts:
                allowed, reason = readonly_check(s)
                if not allowed:
                    return {"ok": False, "error": {"message": reason, "stage": "readonly"},
                            "elapsed_ms": round((time.time() - started) * 1000, 1)}

        cur = conn.cursor()
        results = []
        last_cols = []
        last_rows = []
        for st in stmts:
            t0 = time.time()
            cur.execute(st)
            cols = []
            rows = []
            truncated = False
            try:
                desc = cur.description
            except Exception:
                desc = None
            if desc:
                cols = [str(d[0]) for d in desc]
                fetched = cur.fetchall()
                if len(fetched) > max_rows:
                    fetched = fetched[:max_rows]
                    truncated = True
                rows = [[jsonable(x) for x in r] for r in fetched]
                last_cols, last_rows = cols, rows
            elif cols:
                pass
            results.append({
                "statement": st[:200],
                "columns": cols,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
            })
        cur.close()

        try:
            if not readonly:
                conn.commit()
        except Exception:
            pass

        total_ms = round((time.time() - started) * 1000, 1)
        return {"ok": True, "columns": last_cols, "rows": last_rows,
                "row_count": len(last_rows),
                "statements": results,
                "truncated": any(r["truncated"] for r in results),
                "elapsed_ms": total_ms,
                "readonly": readonly}
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, e)
        return {"ok": False, "error": {"message": msg, "stage": "execute",
                                       "detail": None},
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "readonly": readonly}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 全局状态
class AppState(object):
    cfg = None
    auth = None
    sessions = None
    limiter = None
    captchas = None
    init_token = None
    started_at = time.time()
    log = None
    # 多账号并发时，改 config.json 的几个接口要串行化（查询本身不锁，每请求独立连接）
    cfg_lock = threading.Lock()

    @classmethod
    def logmsg(cls, msg):
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        try:
            if cls.log:
                cls.log.write(line + "\n")
                cls.log.flush()
        except Exception:
            pass


# ---------------------------------------------------------------- HTTP
PUBLIC_PATHS = ("/login", "/api/captcha", "/api/login", "/api/setup", "/api/session")


class Handler(BaseHTTPRequestHandler):
    server_version = "SQLWebCommon/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---- 基础设施 ----
    def _base(self):
        b = (AppState.cfg or {}).get("base_path") or ""
        return b.strip().rstrip("/")

    def _strip_base(self, path):
        b = self._base()
        if not b:
            return path
        if path == b:
            return "/"
        if path.startswith(b + "/"):
            return path[len(b):]
        return "\x00__outside_base__"

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=jsonable).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _png(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _raw(self, body, ctype="text/html; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, loc):
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _file(self, path):
        if not os.path.exists(path):
            msg = ("<h1>500 缺少文件</h1><p>%s 不存在，请确认包内文件完整。</p>"
                   % os.path.basename(path)).encode("utf-8")
            return self._raw(msg)

        with open(path, "rb") as f:
            body = f.read()
        base = self._base()
        inject = ("<script>window.__BASE__=%s;window.__VERSION__=%s;</script>"
                  % (json.dumps(base), json.dumps(APP_VERSION))).encode("utf-8")
        body = body.replace(b"<head>", b"<head>" + inject, 1)
        self._raw(body)

    def _ip(self):
        return self.client_address[0] if self.client_address else "?"

    def _token(self):
        return parse_cookie(self.headers.get("Cookie")).get("sw_session")

    def _session(self):
        return AppState.sessions.get(self._token())

    def _cur_user(self):
        return (self._session() or {}).get("user") or ""

    def _forbid_ds(self, key):
        return self._json({"ok": False, "error": {
            "message": "当前账号没有访问数据源「%s」的权限" % (key or ""),
            "stage": "perm"}}, 403)

    def _need_auth(self):
        """未登录返回 True（并已输出 401）。"""
        if self._session():
            return False
        self._json({"ok": False, "error": {"message": "未登录或会话已失效", "stage": "auth"}}, 401)
        return True

    def _err(self, msg, code=500, stage="server"):
        self._json({"ok": False, "error": {"message": msg, "stage": stage}}, code)

    def _ct(self):
        return (AppState.cfg or {}).get("connect_timeout", 10)

    # ---- 登录态判断 ----
    def _session_info(self):
        s = self._session()
        return {
            "ok": True,
            "initialized": AppState.auth.initialized,
            "logged_in": bool(s),
            "user": (s or {}).get("user"),
            "version": APP_VERSION,
            "title": (AppState.cfg or {}).get("title"),
            "base_path": self._base(),
        }

    # ---- 验证码 ----
    def _captcha(self):
        code, png = captcha.new_captcha()
        cid = AppState.captchas.put(code)
        b64 = base64.b64encode(png).decode("ascii")
        self._json({"ok": True, "id": cid, "image": "data:image/png;base64," + b64})

    # ---- 登录 / 初始化 / 登出 ----
    def _post_login(self, payload):
        ip = self._ip()
        user = (payload.get("username") or "").strip()
        pwd = payload.get("password") or ""
        if not AppState.auth.initialized:
            return self._json({"ok": False, "error": {
                "message": "服务尚未初始化，请先设置管理员账号", "stage": "setup"}}, 400)

        # 限速按「IP+账号」：同一台机器多人用不同账号登录时，一个人输错不会牵连别人
        rkey = "%s|%s" % (ip, user)
        ok_rate, wait = AppState.limiter.allow(rkey)
        if not ok_rate:
            return self._json({"ok": False, "error": {
                "message": "登录失败次数过多，请 %d 秒后重试" % wait, "stage": "rate"}}, 429)

        code = AppState.captchas.take(payload.get("captcha_id"))
        given = (payload.get("captcha") or "").strip()
        if code is None:
            return self._json({"ok": False, "error": {
                "message": "验证码已失效，请刷新后重试", "stage": "captcha"}}, 400)
        if given != code:
            AppState.limiter.fail(rkey)
            return self._json({"ok": False, "error": {
                "message": "验证码不正确", "stage": "captcha"}}, 400)

        if not AppState.auth.verify(user, pwd):
            AppState.limiter.fail(rkey)
            return self._json({"ok": False, "error": {
                "message": "用户名或口令不正确", "stage": "credential"}}, 401)

        AppState.limiter.reset(rkey)
        token = AppState.sessions.create(user, ip)
        cookie = "sw_session=%s; Path=%s; HttpOnly; SameSite=Strict; Max-Age=%d" % (
            token, (self._base() or "") + "/", SESSION_TTL)
        body = json.dumps({"ok": True, "user": user,
                           "role": (AppState.auth.get(user) or {}).get("role") or "user",
                           "can_manage": AppState.auth.is_admin(user)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        AppState.logmsg("登录成功 user=%s ip=%s 在线会话=%d"
                        % (user, ip, AppState.sessions.count()))

    def _post_setup(self, payload):
        if AppState.auth.initialized:
            return self._json({"ok": False, "error": {
                "message": "服务已初始化，不能重复设置", "stage": "setup"}}, 400)
        if not AppState.init_token:
            return self._json({"ok": False, "error": {
                "message": "服务未处于初始化模式", "stage": "setup"}}, 400)
        given = (payload.get("token") or "").strip()
        if not secrets.compare_digest(given, AppState.init_token):
            return self._json({"ok": False, "error": {
                "message": "初始化令牌不正确，请查看启动日志里打印的令牌", "stage": "setup"}}, 400)
        user = (payload.get("username") or "").strip() or "admin"
        pwd = payload.get("password") or ""
        try:
            AppState.auth.set_password(user, pwd)
        except ValueError as e:
            return self._json({"ok": False, "error": {
                "message": str(e), "stage": "setup"}}, 400)
        AppState.init_token = None
        AppState.logmsg("管理员账号已初始化 user=%s" % user)
        return self._json({"ok": True, "user": user})

    def _post_logout(self):
        token = self._token()
        if token:
            AppState.sessions.destroy(token)
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "sw_session=; Path=%s; HttpOnly; SameSite=Strict; Max-Age=0"
                         % ((self._base() or "") + "/"))
        self.end_headers()
        self.wfile.write(body)

    # ---- 数据源管理 ----
    def _ds_test(self, payload):
        """用未保存的参数试连。失败时把驱动原始报错原样返回。"""
        d = payload.get("ds") or {}
        driver_name = (d.get("driver") or "").strip().lower()
        try:
            drv = drivers.get_driver(driver_name)
        except ValueError as e:
            return self._json({"ok": False, "error": {"message": str(e), "stage": "driver"}}, 400)

        ok, info = drv.available()
        if not ok:
            return self._json({"ok": False, "error": {
                "message": "%s 驱动不可用：%s" % (drv.label, info), "stage": "driver"}}, 400)

        cfg = dict(d)
        if not cfg.get("key"):
            cfg["key"] = "test"
        started = time.time()
        conn = None
        try:
            conn = drv.connect(cfg, timeout=self._ct(),
                               statement_timeout_ms=(AppState.cfg or {}).get("statement_timeout_ms", 30000))
            info = drv.server_info(conn)
            try:
                sch = drv.schemas(conn)
            except Exception:
                sch = []
            return self._json({
                "ok": True,
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "server": info,
                "schemas": sch[:50],
                "driver": driver_name,
                "module": drv._modname,
            })
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "%s: %s" % (type(e).__name__, e),
                "stage": "connect", "detail": None},
                "elapsed_ms": round((time.time() - started) * 1000, 1)}, 200)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _ds_save(self, payload):
        d = payload.get("ds") or {}
        driver_name = (d.get("driver") or "").strip().lower()
        if driver_name not in drivers.DRIVER_NAMES:
            return self._json({"ok": False, "error": {
                "message": "不支持的数据库类型: %s" % driver_name, "stage": "param"}}, 400)
        label = (d.get("label") or "").strip()
        if not label:
            return self._json({"ok": False, "error": {
                "message": "名称不能为空", "stage": "param"}}, 400)

        if driver_name != "sqlite":
            if not (d.get("host") or "").strip():
                return self._json({"ok": False, "error": {
                    "message": "主机地址不能为空", "stage": "param"}}, 400)

        key = (d.get("key") or "").strip()
        cfg = AppState.cfg
        dbs = cfg.get("databases") or []

        entry = {
            "key": key,
            "label": label,
            "driver": driver_name,
            "host": (d.get("host") or "").strip(),
            "port": d.get("port") or drivers.DEFAULT_PORTS.get(driver_name),
            "user": (d.get("user") or "").strip(),
            "password": d.get("password") or "",
            "dbname": (d.get("dbname") or "").strip(),
            "path": (d.get("path") or "").strip(),
            "readonly": bool(d.get("readonly", True)),
        }
        if driver_name == "sqlite":
            entry["dbname"] = entry["path"]
            entry["host"] = ""
            entry["port"] = ""

        # 可见模式白名单：对象树与元数据接口只暴露这些 schema（留空 = 全部）
        vs = d.get("visible_schemas")
        if isinstance(vs, str):
            s = vs.replace("，", ",").replace("；", ",").replace(";", ",").replace("\n", ",")
            vs = s.split(",")
        elif not isinstance(vs, (list, tuple)):
            vs = []
        entry["visible_schemas"] = [str(x).strip() for x in vs if str(x).strip()]

        exists = None
        for x in dbs:
            if x.get("key") == key and key:
                exists = x
                break

        if exists is not None:
            # 更新：密码留空表示沿用原密码
            if not entry.get("password"):
                entry["password"] = exists.get("password") or ""
            exists.clear()
            exists.update(entry)
            AppState.logmsg("更新数据源 key=%s driver=%s" % (key, driver_name))
        else:
            if not key:
                key = "ds%d" % int(time.time() * 1000)
                entry["key"] = key
            if any(x.get("key") == key for x in dbs):
                return self._json({"ok": False, "error": {
                    "message": "数据源标识重复: %s" % key, "stage": "param"}}, 400)
            dbs.append(entry)
            cfg["databases"] = dbs
            if not cfg.get("default_db"):
                cfg["default_db"] = key
            AppState.logmsg("新增数据源 key=%s driver=%s" % (key, driver_name))

        try:
            save_config(cfg)
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "写入 config.json 失败: %s" % e, "stage": "persist"}}, 500)
        return self._json({"ok": True, "key": key})

    def _ds_delete(self, payload):
        key = (payload.get("key") or "").strip()
        if not key:
            return self._json({"ok": False, "error": {
                "message": "缺少 key", "stage": "param"}}, 400)
        cfg = AppState.cfg
        dbs = cfg.get("databases") or []
        before = len(dbs)
        cfg["databases"] = [d for d in dbs if d.get("key") != key]
        if len(cfg["databases"]) == before:
            return self._json({"ok": False, "error": {
                "message": "数据源不存在: %s" % key, "stage": "param"}}, 404)
        if cfg.get("default_db") == key:
            cfg["default_db"] = (cfg["databases"][0]["key"] if cfg["databases"] else "")
        try:
            save_config(cfg)
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "写入 config.json 失败: %s" % e, "stage": "persist"}}, 500)
        AppState.logmsg("删除数据源 key=%s" % key)
        return self._json({"ok": True})

    def _ds_default(self, payload):
        key = (payload.get("key") or "").strip()
        cfg = AppState.cfg
        if not any(d.get("key") == key for d in (cfg.get("databases") or [])):
            return self._json({"ok": False, "error": {
                "message": "数据源不存在: %s" % key, "stage": "param"}}, 404)
        cfg["default_db"] = key
        try:
            save_config(cfg)
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "写入 config.json 失败: %s" % e, "stage": "persist"}}, 500)
        return self._json({"ok": True})

    # ---- Redis 实例管理（仅管理员）----
    def _redis_inst_from_payload(self, d):
        """把表单里的 Redis 实例规整成内部结构；返回 (inst, 错误响应)。"""
        def _int(name, default, lo, hi):
            v = d.get(name)
            if v in (None, ""):
                return default, None
            try:
                iv = int(v)
            except (TypeError, ValueError):
                return None, "「%s」必须是整数" % name
            if iv < lo or iv > hi:
                return None, "「%s」超出范围（%d-%d）" % (name, lo, hi)
            return iv, None

        port, e1 = _int("port", 6379, 1, 65535)
        if e1:
            return None, e1
        db, e2 = _int("db", 0, 0, 255)
        if e2:
            return None, e2
        host = (d.get("host") or "").strip()
        if not host:
            return None, "主机地址不能为空"
        label = (d.get("label") or "").strip() or host
        return {
            "key": (d.get("key") or "").strip(),
            "label": label,
            "host": host,
            "port": port,
            "db": db,
            "username": (d.get("username") or "").strip(),
            "password": d.get("password") or "",
            "readonly": bool(d.get("readonly", True)),
            "timeout": d.get("timeout") or 5,
        }, None

    def _redis_test(self, payload):
        d = payload.get("inst") or {}
        if (d.get("host") or "").strip():
            inst, msg = self._redis_inst_from_payload(d)
            if inst is None:
                return self._json({"ok": False, "error": {
                    "message": msg, "stage": "param"}}, 400)
        else:
            inst = get_redis(AppState.cfg, (d.get("key") or "").strip())
            if inst is None:
                return self._json({"ok": False, "error": {
                    "message": "缺少主机地址", "stage": "param"}}, 400)
        started = time.time()
        try:
            with redis_panel.redis_conn(inst) as c:
                c.execute("PING")
                raw = c.execute("INFO") or ""
                kv = {}
                for line in raw.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        k, v = line.split(":", 1)
                        kv[k.strip()] = v.strip()
                try:
                    dbsize = c.execute("DBSIZE")
                except Exception:
                    dbsize = None
            return self._json({
                "ok": True, "version": kv.get("redis_version", ""),
                "role": kv.get("role", ""), "dbsize": dbsize,
                "used_memory_human": kv.get("used_memory_human", ""),
                "elapsed_ms": round((time.time() - started) * 1000, 1),
            })
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "%s: %s" % (type(e).__name__, e), "stage": "connect"},
                "elapsed_ms": round((time.time() - started) * 1000, 1)})

    def _redis_save(self, payload):
        d = payload.get("inst") or {}
        entry, msg = self._redis_inst_from_payload(d)
        if entry is None:
            return self._json({"ok": False, "error": {
                "message": msg, "stage": "param"}}, 400)
        cfg = AppState.cfg
        raw = cfg.get("redis") or []
        if isinstance(raw, dict):
            raw = [raw]
        raw = [dict(x) for x in raw if isinstance(x, dict)]

        key = entry["key"]
        exists = None
        for x in raw:
            if key and (x.get("key") or "") == key:
                exists = x
                break
        if exists is not None:
            # 口令留空 = 沿用原口令（表单不回显口令）
            if not entry.get("password"):
                entry["password"] = exists.get("password") or ""
            if not entry.get("username"):
                entry["username"] = exists.get("username") or ""
            exists.clear()
            exists.update(entry)
        else:
            if not key:
                key = "redis%d" % int(time.time() * 1000)
                entry["key"] = key
            if any((x.get("key") or "") == key for x in raw):
                return self._json({"ok": False, "error": {
                    "message": "Redis 标识重复: %s" % key, "stage": "param"}}, 400)
            raw.append(entry)
        cfg["redis"] = raw
        cfg["_redis"] = normalize_redis(cfg)["_redis"]
        try:
            save_config(cfg)
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "写入 config.json 失败: %s" % e, "stage": "persist"}}, 500)
        AppState.logmsg("保存 Redis 实例 key=%s host=%s:%s db=%s"
                        % (key, entry.get("host"), entry.get("port"), entry.get("db")))
        return self._json({"ok": True, "key": key})

    def _redis_delete(self, payload):
        key = (payload.get("key") or "").strip()
        if not key:
            return self._json({"ok": False, "error": {
                "message": "缺少 key", "stage": "param"}}, 400)
        cfg = AppState.cfg
        raw = cfg.get("redis") or []
        if isinstance(raw, dict):
            raw = [raw]
        before = len(raw)
        raw = [x for x in raw if isinstance(x, dict) and (x.get("key") or "") != key]
        if len(raw) == before:
            return self._json({"ok": False, "error": {
                "message": "Redis 实例不存在: %s" % key, "stage": "param"}}, 404)
        cfg["redis"] = raw
        cfg["_redis"] = normalize_redis(cfg)["_redis"]
        try:
            save_config(cfg)
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "写入 config.json 失败: %s" % e, "stage": "persist"}}, 500)
        AppState.logmsg("删除 Redis 实例 key=%s" % key)
        return self._json({"ok": True})

    # ---- GET ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = self._strip_base(parsed.path)
        qs = parse_qs(parsed.query)

        if path == "/login":
            return self._file(LOGIN_PATH)

        if path in ("/", "/index.html"):
            if not self._session():
                return self._redirect((self._base() or "") + "/login")
            return self._file(INDEX_PATH)

        if path == "/api/captcha":
            return self._captcha()

        if path == "/api/session":
            return self._json(self._session_info())

        if path.startswith("/api/"):
            if self._need_auth():
                return
        else:
            return self.send_error(404, "Not Found")

        try:
            if path == "/api/config":
                cfg = AppState.cfg
                user = self._cur_user()
                my = visible_dbs(user)
                default_db = cfg.get("default_db")
                if not any(d.get("key") == default_db for d in my):
                    default_db = my[0]["key"] if my else ""
                return self._json({
                    "ok": True,
                    "version": APP_VERSION,
                    "title": cfg.get("title"),
                    "readonly": bool(cfg.get("readonly", True)),
                    "max_rows": cfg.get("max_rows", 1000),
                    "default_db": default_db,
                    "user": user,
                    "role": (AppState.auth.get(user) or {}).get("role") or "user",
                    "can_manage": AppState.auth.is_admin(user),
                    "databases": [public_ds(d) for d in my],
                    "redis": [public_redis(i) for i in visible_redis(user)],
                })

            if path == "/api/drivers":
                return self._json({"ok": True, "data": drivers.list_drivers()})

            if path == "/api/ds":
                return self._json({"ok": True, "data": [
                    public_ds(d) for d in visible_dbs(self._cur_user())]})

            if path == "/api/users":
                return self._get_users()

            if path.startswith("/api/redis/"):
                return self._redis_get(path, qs)

            dbkey = (qs.get("db") or [None])[0]
            if not dbkey:
                mine = visible_dbs(self._cur_user())
                dbkey = mine[0]["key"] if mine else None
            ds = get_db(AppState.cfg, dbkey)
            if not ds_allowed(self._cur_user(), ds.get("key")):
                return self._forbid_ds(ds.get("key"))
            drv = drivers.get_driver(ds.get("driver"))

            # ?database=<库名> 用同一套地址/口令临时改连到别的库（PostgreSQL / SQL Server
            # 一个连接只绑一个库，跨库浏览只能换 dbname 重连），不写回 config.json。
            dbname = (qs.get("database") or [None])[0]
            if dbname:
                ds = dict(ds, dbname=dbname)

            if path == "/api/databases":
                # 顶栏下拉要按数据源分组列出库，所以每个源都要能单独问一句「你有哪些库」。
                # 只读连接、不落盘；取不到就返回空数组，由前端退化成单一选项。
                data = self._with_conn(ds, drv, lambda c: drv.databases(c)) or []
                return self._json({"ok": True, "db": ds["key"], "data": data})

            if path == "/api/overview":
                return self._json({"ok": True, "db": ds["key"], "data": self._overview(ds, drv)})
            if path == "/api/schemas":
                data = self._with_conn(ds, drv, lambda c: drv.schemas(c))
                return self._json({"ok": True, "db": ds["key"], "data": filter_schemas(ds, data)})
            if path == "/api/tables":
                schema = (qs.get("schema") or ["public"])[0]
                if not schema_in_scope(ds, schema):
                    return self._err("模式「%s」不在该数据源开放范围内" % schema, 403, "perm")
                return self._json({"ok": True, "db": ds["key"],
                                   "data": self._with_conn(ds, drv, lambda c: drv.tables(c, schema))})
            if path == "/api/columns":
                schema = (qs.get("schema") or ["public"])[0]
                if not schema_in_scope(ds, schema):
                    return self._err("模式「%s」不在该数据源开放范围内" % schema, 403, "perm")
                table = (qs.get("table") or [""])[0]
                return self._json({"ok": True, "db": ds["key"],
                                   "data": self._with_conn(ds, drv, lambda c: drv.columns(c, schema, table))})
            if path == "/api/search_tables":
                # 跨模式搜表：只在「可见模式」白名单内扫，超时/超量就返回已找到的部分
                kw = (qs.get("q") or [""])[0].strip()
                try:
                    limit = int((qs.get("limit") or ["300"])[0] or 300)
                except Exception:
                    limit = 300
                limit = max(1, min(limit, 1000))
                if not kw:
                    return self._json({"ok": True, "db": ds["key"], "data": [],
                                       "scanned": 0, "truncated": False, "elapsed_ms": 0})
                t0 = time.time()
                low = kw.lower()

                def _scan(c):
                    hits, scanned, truncated = [], 0, False
                    for s in filter_schemas(ds, drv.schemas(c)):
                        if time.time() - t0 > 8.0:
                            truncated = True
                            break
                        scanned += 1
                        try:
                            tabs = drv.tables(c, s) or []
                        except Exception:
                            continue
                        for t in tabs:
                            if low in str(t).lower():
                                hits.append({"schema": s, "table": t})
                                if len(hits) >= limit:
                                    return hits, scanned, True
                    return hits, scanned, truncated

                hits, scanned, truncated = self._with_conn(ds, drv, _scan)
                return self._json({"ok": True, "db": ds["key"], "data": hits,
                                   "scanned": scanned, "truncated": truncated,
                                   "elapsed_ms": round((time.time() - t0) * 1000, 1)})
        except Exception as e:
            return self._err("%s: %s" % (type(e).__name__, e), stage="meta")

        self.send_error(404, "Not Found")

    def _with_conn(self, ds, drv, fn):
        conn = drv.connect(ds, timeout=self._ct(),
                           statement_timeout_ms=(AppState.cfg or {}).get("statement_timeout_ms", 30000))
        try:
            return fn(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _schema_counts(self, conn, drv, schemas):
        """可见模式各自的表数。统计失败只影响右侧数字，绝不能让对象树加载失败。"""
        try:
            counts = drv.schema_table_counts(conn) or {}
        except Exception:
            return {}
        upper = {}
        for k, v in counts.items():
            upper[str(k).upper()] = v
        out = {}
        for s in schemas:
            v = counts.get(s)
            if v is None:
                v = upper.get(str(s).upper())
            if v is not None:
                out[s] = v
        return out

    def _overview(self, ds, drv):
        conn = drv.connect(ds, timeout=self._ct(),
                           statement_timeout_ms=(AppState.cfg or {}).get("statement_timeout_ms", 30000))
        try:
            out = drv.overview(conn)
            out["driver"] = ds.get("driver")
            out["db"] = ds.get("key")
            out["schemas"] = filter_schemas(ds, out.get("schemas") or [])
            out["default_schema"] = (out.get("schemas") or [""])[0]
            # 白名单内的每个模式有几张表：schema 行右侧显示（如 public 73）
            out["schema_counts"] = self._schema_counts(conn, drv, out.get("schemas") or [])
            return out
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _redis_get(self, path, qs):
        me = self._cur_user()
        insts = visible_redis(me)
        if not insts:
            return self._json({"ok": False, "error": {
                "message": "还没有可用的 Redis 实例（管理员可在 Redis 面板里添加）",
                "stage": "config"}}, 400)
        key = (qs.get("redis") or [None])[0]
        inst = None
        for i in insts:
            if key and i.get("key") == key:
                inst = i
                break
        if inst is None:
            if key:
                return self._json({"ok": False, "error": {
                    "message": "当前账号没有访问 Redis 实例「%s」的权限" % key,
                    "stage": "perm"}}, 403)
            inst = insts[0]
        try:
            if path == "/api/redis/overview":
                return self._json({"ok": True, "redis": inst["key"],
                                   "data": redis_panel.redis_overview(inst)})
            if path == "/api/redis/scan":
                pattern = (qs.get("pattern") or ["*"])[0] or "*"
                cursor = (qs.get("cursor") or ["0"])[0] or "0"
                count = (qs.get("count") or [None])[0] or 200
                r = redis_panel.redis_scan(inst, pattern, cursor, count)
                r["ok"] = True
                r["redis"] = inst["key"]
                return self._json(r)
            if path == "/api/redis/key":
                k = (qs.get("key") or [""])[0]
                if not k:
                    return self._json({"ok": False, "error": {
                        "message": "缺少 key 参数", "stage": "param"}}, 400)
                d = redis_panel.redis_key_detail(inst, k, 200)
                if d is None:
                    return self._json({"ok": False, "error": {
                        "message": "key 不存在：%s" % k, "stage": "key"}})
                return self._json({"ok": True, "redis": inst["key"], "data": d})
        except Exception as e:
            return self._json({"ok": False, "error": {"message": "%s: %s" % (type(e).__name__, e),
                                                      "stage": "redis"}})
        return self.send_error(404, "Not Found")

    # ---- POST ----
    def do_POST(self):
        parsed = urlparse(self.path)
        path = self._strip_base(parsed.path)

        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "请求体解析失败: %s" % e, "stage": "parse"}}, 400)

        if path == "/api/login":
            return self._post_login(payload)
        if path == "/api/setup":
            return self._post_setup(payload)
        if path == "/api/logout":
            return self._post_logout()

        if path.startswith("/api/") or path.startswith("/api"):
            if self._need_auth():
                return

        try:
            if path == "/api/ds/test":
                if not AppState.auth.is_admin(self._cur_user()):
                    return self._json({"ok": False, "error": {
                        "message": "只有管理员可以测试连接", "stage": "perm"}}, 403)
                return self._ds_test(payload)
            if path in ("/api/ds/save", "/api/ds/delete", "/api/ds/default"):
                if not AppState.auth.is_admin(self._cur_user()):
                    return self._json({"ok": False, "error": {
                        "message": "只有管理员可以修改数据源", "stage": "perm"}}, 403)
                # 多人并发改配置时串行化，避免两个请求同时 append 丢一条
                with AppState.cfg_lock:
                    if path == "/api/ds/save":
                        return self._ds_save(payload)
                    if path == "/api/ds/delete":
                        return self._ds_delete(payload)
                    return self._ds_default(payload)
            if path == "/api/redis/test":
                if not AppState.auth.is_admin(self._cur_user()):
                    return self._json({"ok": False, "error": {
                        "message": "只有管理员可以测试 Redis 连接", "stage": "perm"}}, 403)
                return self._redis_test(payload)
            if path in ("/api/redis/save", "/api/redis/delete"):
                if not AppState.auth.is_admin(self._cur_user()):
                    return self._json({"ok": False, "error": {
                        "message": "只有管理员可以修改 Redis 实例", "stage": "perm"}}, 403)
                with AppState.cfg_lock:
                    if path == "/api/redis/save":
                        return self._redis_save(payload)
                    return self._redis_delete(payload)
            if path == "/api/password":
                return self._post_password(payload)
            if path == "/api/users":
                return self._post_users(payload)
            if path == "/api/query":
                return self._post_query(payload)
            if path == "/api/redis/cmd":
                return self._post_redis(payload)
        except Exception as e:
            return self._err("%s: %s" % (type(e).__name__, e), stage="server")

        self.send_error(404, "Not Found")

    def _post_password(self, payload):
        old = payload.get("old") or ""
        new = payload.get("new") or ""
        user = self._cur_user()
        try:
            AppState.auth.change_password(user, old, new)
        except ValueError as e:
            return self._json({"ok": False, "error": {"message": str(e), "stage": "password"}}, 400)
        except Exception as e:
            return self._json({"ok": False, "error": {"message": "%s: %s" % (type(e).__name__, e),
                                                      "stage": "password"}}, 500)
        # 只注销该账号自己的会话：别人正在跑的查询不受影响
        n = AppState.sessions.destroy_user(user)
        AppState.logmsg("口令已修改 user=%s，注销该账号 %d 个会话" % (user, n))
        return self._json({"ok": True, "message": "口令已修改，请重新登录"})

    # ---- 账号管理（仅管理员）----
    def _get_users(self):
        me = self._cur_user()
        if not AppState.auth.is_admin(me):
            return self._json({"ok": False, "error": {
                "message": "只有管理员可以管理账号", "stage": "perm"}}, 403)
        return self._json({
            "ok": True,
            "me": me,
            "data": AppState.auth.users(),
            "databases": [public_ds(d) for d in (AppState.cfg.get("databases") or [])],
            "redis": [public_redis(i) for i in (AppState.cfg.get("_redis") or [])],
            "online": AppState.sessions.count(),
        })

    def _post_users(self, payload):
        me = self._cur_user()
        if not AppState.auth.is_admin(me):
            return self._json({"ok": False, "error": {
                "message": "只有管理员可以管理账号", "stage": "perm"}}, 403)

        act = (payload.get("action") or "save").strip()
        name = (payload.get("user") or "").strip()
        pwd = payload.get("password") or ""
        role = (payload.get("role") or "user").strip()
        if role not in ("admin", "user"):
            role = "user"

        try:
            if act == "delete":
                if name == me:
                    return self._json({"ok": False, "error": {
                        "message": "不能删除当前登录的管理员自己", "stage": "param"}}, 400)
                AppState.auth.remove_user(name)
                n = AppState.sessions.destroy_user(name)
                AppState.logmsg("账号已删除 user=%s，注销会话 %d 个" % (name, n))
                return self._json({"ok": True, "message": "账号已删除：%s" % name})

            if not name:
                return self._json({"ok": False, "error": {
                    "message": "用户名不能为空", "stage": "param"}}, 400)

            allow = ([str(x) for x in (payload.get("allow_dbs") or [])]
                     if "allow_dbs" in payload else AuthStore.KEEP)
            allow_r = ([str(x) for x in (payload.get("allow_redis") or [])]
                       if "allow_redis" in payload else AuthStore.KEEP)

            if AppState.auth.get(name):
                if act == "reset":
                    if not pwd:
                        return self._json({"ok": False, "error": {
                            "message": "重设口令不能为空", "stage": "param"}}, 400)
                    AppState.auth.set_password(name, pwd)
                    msg = "口令已重设：%s" % name
                else:
                    AppState.auth.update_user(name, role=role, allow_dbs=allow,
                                              allow_redis=allow_r, password=(pwd or None))
                    msg = "账号已更新：%s" % name
            else:
                if not pwd:
                    return self._json({"ok": False, "error": {
                        "message": "新建账号必须设置口令（≥8 位）", "stage": "param"}}, 400)
                AppState.auth.add_user(name, pwd, role,
                                       None if allow is AuthStore.KEEP else allow,
                                       None if allow_r is AuthStore.KEEP else allow_r)
                msg = "账号已新建：%s" % name

            # 角色/授权/口令任一变化，旧的会话一律作废，避免越权继续跑
            n = AppState.sessions.destroy_user(name)
            AppState.logmsg("%s，注销该账号会话 %d 个" % (msg, n))
            return self._json({"ok": True, "message": msg})
        except ValueError as e:
            return self._json({"ok": False, "error": {"message": str(e), "stage": "users"}}, 400)
        except Exception as e:
            return self._json({"ok": False, "error": {
                "message": "%s: %s" % (type(e).__name__, e), "stage": "users"}}, 500)

    def _post_query(self, payload):
        sql = (payload.get("sql") or "").strip()
        if not sql:
            return self._json({"ok": False, "error": {
                "message": "SQL 为空", "stage": "param"}}, 400)
        max_rows = payload.get("max_rows") or (AppState.cfg.get("max_rows", 1000))
        try:
            max_rows = max(1, min(int(max_rows), 20000))
        except Exception:
            max_rows = 1000
        ds = get_db(AppState.cfg, payload.get("db"))
        if not ds_allowed(self._cur_user(), ds.get("key")):
            return self._forbid_ds(ds.get("key"))
        # 跨库查询：前端在对象树里选了别的库时，用同一套地址/口令临时换 dbname 重连
        dbname = payload.get("database")
        if dbname:
            ds = dict(ds, dbname=dbname)
        schema = payload.get("schema") or None
        if schema and not schema_in_scope(ds, schema):
            return self._err("模式「%s」不在该数据源开放范围内" % schema, 403, "perm")
        result = run_query(AppState.cfg, ds, sql, max_rows, schema=schema)
        result["db"] = ds.get("key")
        return self._json(result)

    def _post_redis(self, payload):
        me = self._cur_user()
        insts = visible_redis(me)
        if not insts:
            return self._json({"ok": False, "error": {
                "message": "还没有可用的 Redis 实例", "stage": "config"}}, 400)
        key = payload.get("redis")
        inst = None
        for i in insts:
            if key and i.get("key") == key:
                inst = i
                break
        if inst is None:
            if key:
                return self._json({"ok": False, "error": {
                    "message": "当前账号没有访问 Redis 实例「%s」的权限" % key,
                    "stage": "perm"}}, 403)
            inst = insts[0]
        raw = (payload.get("cmd") or "").strip()
        if not raw:
            return self._json({"ok": False, "error": {
                "message": "命令为空", "stage": "parse"}, "elapsed_ms": 0}, 400)
        import shlex as _shlex
        try:
            argv = _shlex.split(raw)
        except ValueError as e:
            return self._json({"ok": False, "error": {
                "message": "命令解析失败：%s" % e, "stage": "parse"}, "elapsed_ms": 0})
        if not argv:
            return self._json({"ok": False, "error": {
                "message": "命令为空", "stage": "parse"}, "elapsed_ms": 0})
        r = redis_panel.redis_run(inst, argv, bool(inst.get("readonly", True)))
        r["redis"] = inst.get("key")
        return self._json(r)


# ---------------------------------------------------------------- 启动
def normalize_redis(cfg):
    """把 redis 配置规整成内部结构（_redis），沿用 sqlweb v2.3 的字段名。"""
    out = []
    raw = cfg.get("redis") or []
    if isinstance(raw, dict):
        raw = [raw]
    for i, x in enumerate(raw):
        if not isinstance(x, dict):
            continue
        inst = {
            "key": x.get("key") or ("redis%d" % (i + 1)),
            "label": x.get("label") or x.get("key") or ("Redis %d" % (i + 1)),
            "host": x.get("host") or "127.0.0.1",
            "port": int(x.get("port") or 6379),
            "password": x.get("password") or "",
            "username": x.get("username") or "",
            "db": int(x.get("db") or 0),
            "readonly": bool(x.get("readonly", True)),
            "timeout": float(x.get("timeout") or 5),
        }
        out.append(inst)
    cfg["_redis"] = out
    return cfg


def main():
    ap = argparse.ArgumentParser(description="sqlweb_offline_common")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--base-path", dest="base_path", default=None,
                    help="nginx 子路径前缀（如 /sqlweb）；给了就写回状态库")
    ap.add_argument("--config", default=None)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    # 状态库：sqlweb.db（单文件 SQLite）。首次启动会把老的 config.json / auth.json
    # 自动导进来并把旧文件改名留档，之后一律以库为准。
    global STORE
    dbpath = str(args.config) if (args.config and str(args.config).lower().endswith(".db")) else DB_PATH
    STORE = state_store.SqliteStore(dbpath)
    legacy_cfg = (str(args.config)
                  if (args.config and str(args.config).lower().endswith(".json")) else CONFIG_PATH)
    mig = STORE.migrate_from_json(legacy_cfg, AUTH_PATH)
    if mig.get("config") or mig.get("auth"):
        print("已把旧配置迁入 SQLite：数据源 %d 个、Redis 实例 %d 个、账号 %d 个"
              % (mig.get("config", 0), mig.get("redis", 0), mig.get("auth", 0)), flush=True)
        for a in mig.get("archived") or []:
            print("  旧文件已留档: %s" % a, flush=True)
    elif os.path.exists(legacy_cfg):
        # 库已有状态、但目录里又出现 config.json：多为迁移后按老习惯手改它，必须说清楚
        print("提示: 检测到 %s，但配置真源是 %s（SQLite），该文件不再参与读写"
              % (os.path.basename(legacy_cfg), STORE.path), flush=True)
        print("      改端口 / base_path / 数据源：界面里改，或 python3 setconfig.py --set base_path=/sqlweb",
              flush=True)
    cfg = normalize_redis(load_config())
    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port
    if args.base_path is not None:
        bp = (args.base_path or "").strip()
        bp = "" if bp in ("", "/") else ("/" + bp.lstrip("/")).rstrip("/")
        if bp != (cfg.get("base_path") or ""):
            cfg["base_path"] = bp
            save_config(cfg)          # 写回状态库，重启后仍生效
            print("已把 base_path 写回状态库：%r" % bp, flush=True)
    AppState.cfg = cfg

    if args.log:
        try:
            AppState.log = open(args.log, "a", encoding="utf-8")
        except Exception as e:
            print("日志文件打不开: %s" % e, flush=True)

    AppState.auth = AuthStore(AUTH_PATH, store=STORE)
    AppState.sessions = SessionManager()
    AppState.limiter = RateLimiter()
    AppState.captchas = CaptchaStore()

    if not AppState.auth.initialized:
        AppState.init_token = "%06d" % secrets.randbelow(1000000)
        print("", flush=True)
        print("=" * 64, flush=True)
        print("  首次启动：服务尚未初始化管理员账号", flush=True)
        print("  请打开 http://<本机IP>:%s/login 填写账号口令" % cfg.get("port"), flush=True)
        print("  初始化令牌（必填，仅本次启动有效）：", flush=True)
        print("      %s" % AppState.init_token, flush=True)
        print("=" * 64, flush=True)
        print("", flush=True)

    print("sqlweb_offline_common %s" % APP_VERSION, flush=True)
    print("根目录: %s" % HERE, flush=True)
    print("状态库: %s（SQLite 单文件）" % STORE.path, flush=True)
    print("监听: %s:%s" % (cfg.get("host"), cfg.get("port")), flush=True)
    print("访问前缀: %s" % (cfg.get("base_path") or "（根路径，无前缀）"), flush=True)
    for d in drivers.list_drivers():
        print("  驱动 %-12s %-16s %s" % (d["name"], d["label"],
                                       "可用" if d["available"] else "缺失"),
              flush=True)
    print("数据源: %d 个" % len(cfg.get("databases") or []), flush=True)

    srv = ThreadingHTTPServer((cfg.get("host", "0.0.0.0"), int(cfg.get("port", 8899)), ), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，正在退出", flush=True)
    finally:
        try:
            srv.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
