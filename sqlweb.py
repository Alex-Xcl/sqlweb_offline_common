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
import time
import traceback
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import drivers
import captcha
import redis_panel
from auth import (AuthStore, SessionManager, RateLimiter, CaptchaStore,
                  parse_cookie, SESSION_TTL)

APP_VERSION = "common-1.0.0"

CONFIG_PATH = os.path.join(HERE, "config.json")
INDEX_PATH = os.path.join(HERE, "index.html")
LOGIN_PATH = os.path.join(HERE, "login.html")
AUTH_PATH = os.path.join(HERE, "auth.json")

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
    path = path or CONFIG_PATH
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            cfg.update(raw)
    cfg["_path"] = path
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
        dbs.append(d)
    cfg["databases"] = dbs
    if not cfg.get("default_db") and dbs:
        cfg["default_db"] = dbs[0]["key"]
    return cfg


def save_config(cfg, path=None):
    """把配置写回磁盘。只落白名单字段，不把运行时键写进去。"""
    path = path or cfg.get("_path") or CONFIG_PATH
    out = {}
    for k, v in cfg.items():
        if k.startswith("_"):
            continue
        out[k] = v
    out["databases"] = [
        {kk: vv for kk, vv in d.items() if not kk.startswith("_")}
        for d in (cfg.get("databases") or [])
    ]
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
    }


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


def split_statements(sql):
    """按分号切分，尊重引号与括号。"""
    s = strip_comments(sql)
    stmts = []
    buf = []
    depth = 0
    q = None
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
            q = c
            buf.append(c)
        elif c == "(":
            depth += 1
            buf.append(c)
        elif c == ")":
            depth = max(0, depth - 1)
            buf.append(c)
        elif c == ";" and depth == 0:
            t = "".join(buf).strip()
            if t:
                stmts.append(t)
            buf = []
        else:
            buf.append(c)
        i += 1
    t = "".join(buf).strip()
    if t:
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
    return False, "只读模式仅允许查询类语句（select/with/show/explain 等），当前语句以 %s 开头" % head


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
        if not AppState.auth.initialized:
            return self._json({"ok": False, "error": {
                "message": "服务尚未初始化，请先设置管理员账号", "stage": "setup"}}, 400)

        ok_rate, wait = AppState.limiter.allow(ip)
        if not ok_rate:
            return self._json({"ok": False, "error": {
                "message": "登录失败次数过多，请 %d 秒后重试" % wait, "stage": "rate"}}, 429)

        code = AppState.captchas.take(payload.get("captcha_id"))
        given = (payload.get("captcha") or "").strip()
        if code is None:
            return self._json({"ok": False, "error": {
                "message": "验证码已失效，请刷新后重试", "stage": "captcha"}}, 400)
        if given != code:
            AppState.limiter.fail(ip)
            return self._json({"ok": False, "error": {
                "message": "验证码不正确", "stage": "captcha"}}, 400)

        user = (payload.get("username") or "").strip()
        pwd = payload.get("password") or ""
        if not AppState.auth.verify(user, pwd):
            AppState.limiter.fail(ip)
            return self._json({"ok": False, "error": {
                "message": "用户名或口令不正确", "stage": "credential"}}, 401)

        AppState.limiter.reset(ip)
        token = AppState.sessions.create(AppState.auth.user, ip)
        cookie = "sw_session=%s; Path=%s; HttpOnly; SameSite=Strict; Max-Age=%d" % (
            token, (self._base() or "") + "/", SESSION_TTL)
        body = json.dumps({"ok": True, "user": AppState.auth.user}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        AppState.logmsg("登录成功 user=%s ip=%s" % (AppState.auth.user, ip))

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
                return self._json({
                    "ok": True,
                    "version": APP_VERSION,
                    "title": cfg.get("title"),
                    "readonly": bool(cfg.get("readonly", True)),
                    "max_rows": cfg.get("max_rows", 1000),
                    "default_db": cfg.get("default_db"),
                    "user": AppState.auth.user,
                    "databases": [public_ds(d) for d in (cfg.get("databases") or [])],
                    "redis": [
                        {"key": i["key"], "label": i.get("label"), "host": i.get("host"),
                         "port": i.get("port"), "db": i.get("db"),
                         "readonly": i.get("readonly", True)}
                        for i in (cfg.get("_redis") or [])  # 见下方 normalize
                    ],
                })

            if path == "/api/drivers":
                return self._json({"ok": True, "data": drivers.list_drivers()})

            if path == "/api/ds":
                return self._json({"ok": True, "data": [
                    public_ds(d) for d in (AppState.cfg.get("databases") or [])]})

            if path.startswith("/api/redis/"):
                return self._redis_get(path, qs)

            dbkey = (qs.get("db") or [None])[0]
            ds = get_db(AppState.cfg, dbkey)
            drv = drivers.get_driver(ds.get("driver"))

            if path == "/api/overview":
                return self._json({"ok": True, "db": ds["key"], "data": self._overview(ds, drv)})
            if path == "/api/schemas":
                return self._json({"ok": True, "db": ds["key"], "data": self._with_conn(ds, drv, lambda c: drv.schemas(c))})
            if path == "/api/tables":
                schema = (qs.get("schema") or ["public"])[0]
                return self._json({"ok": True, "db": ds["key"],
                                   "data": self._with_conn(ds, drv, lambda c: drv.tables(c, schema))})
            if path == "/api/columns":
                schema = (qs.get("schema") or ["public"])[0]
                table = (qs.get("table") or [""])[0]
                return self._json({"ok": True, "db": ds["key"],
                                   "data": self._with_conn(ds, drv, lambda c: drv.columns(c, schema, table))})
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

    def _overview(self, ds, drv):
        conn = drv.connect(ds, timeout=self._ct(),
                           statement_timeout_ms=(AppState.cfg or {}).get("statement_timeout_ms", 30000))
        try:
            out = drv.overview(conn)
            out["driver"] = ds.get("driver")
            out["db"] = ds.get("key")
            out["default_schema"] = (out.get("schemas") or [""])[0]
            return out
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _redis_get(self, path, qs):
        insts = (AppState.cfg or {}).get("_redis") or []
        if not insts:
            return self._json({"ok": False, "error": {
                "message": "config.json 里还没有配置 Redis（redis 段为空）", "stage": "config"}}, 400)
        key = (qs.get("redis") or [None])[0]
        inst = None
        for i in insts:
            if key and i.get("key") == key:
                inst = i
                break
        if inst is None:
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
                return self._ds_test(payload)
            if path == "/api/ds/save":
                return self._ds_save(payload)
            if path == "/api/ds/delete":
                return self._ds_delete(payload)
            if path == "/api/ds/default":
                return self._ds_default(payload)
            if path == "/api/password":
                return self._post_password(payload)
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
        try:
            AppState.auth.change_password(old, new)
        except ValueError as e:
            return self._json({"ok": False, "error": {"message": str(e), "stage": "password"}}, 400)
        except Exception as e:
            return self._json({"ok": False, "error": {"message": "%s: %s" % (type(e).__name__, e),
                                                      "stage": "password"}}, 500)
        # 改密后让所有会话失效，强制重新登录
        n = AppState.sessions.destroy_all()
        AppState.logmsg("管理员口令已修改，已注销 %d 个会话" % n)
        return self._json({"ok": True, "message": "口令已修改，请重新登录"})

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
        schema = payload.get("schema") or None
        result = run_query(AppState.cfg, ds, sql, max_rows, schema=schema)
        result["db"] = ds.get("key")
        return self._json(result)

    def _post_redis(self, payload):
        insts = (AppState.cfg or {}).get("_redis") or []
        if not insts:
            return self._json({"ok": False, "error": {
                "message": "config.json 里还没有配置 Redis", "stage": "config"}}, 400)
        key = payload.get("redis")
        inst = insts[0]
        for i in insts:
            if key and i.get("key") == key:
                inst = i
                break
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
            "db": int(x.get("db") or 0),
            "readonly": bool(x.get("readonly", True)),
        }
        out.append(inst)
    cfg["_redis"] = out
    return cfg


def main():
    ap = argparse.ArgumentParser(description="sqlweb_offline_common")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    cfg = normalize_redis(load_config(args.config))
    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port
    AppState.cfg = cfg

    if args.log:
        try:
            AppState.log = open(args.log, "a", encoding="utf-8")
        except Exception as e:
            print("日志文件打不开: %s" % e, flush=True)

    AppState.auth = AuthStore(AUTH_PATH)
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
    print("配置文件: %s" % (args.config or CONFIG_PATH), flush=True)
    print("监听: %s:%s" % (cfg.get("host"), cfg.get("port")), flush=True)
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
