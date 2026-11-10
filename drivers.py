# -*- coding: utf-8 -*-
"""
多数据库驱动适配层 —— sqlweb_offline_common

纯标准库，无第三方框架依赖。每个数据库一个 Driver 对象，统一接口：

    name / label / default_port
    available()             -> (bool, 说明)
    connect(cfg, timeout)   -> DB-API connection
    schemas(conn)           -> [schema 名]
    tables(conn, schema)    -> [表名]
    columns(conn, schema, table) -> [[列名, 类型, 是否可空]]
    overview(conn)          -> dict
    server_info(conn)       -> 版本字符串

驱动优先级（按可用性自动降级）：
    postgresql  psycopg2  >  pg8000(纯 Python)
    mysql       PyMySQL（纯 Python）
    sqlserver   pymssql
    oracle      oracledb（thin 模式，无需 Oracle 客户端）
    dameng      dmPython（官方，Linux/Windows） > dmpy（纯 Python，含 macOS）
    sqlite      标准库 sqlite3

元数据查询统一使用"单引号转义拼接"而不是参数化，原因是各驱动 paramstyle
不一致（? / %s / :1），而 schema/table 名来自界面点选，转义后无注入风险。
这样一套 SQL 骨架就能跨驱动复用。
"""
import os
import sqlite3

DRIVER_NAMES = ("postgresql", "mysql", "sqlserver", "oracle", "dameng", "sqlite")

DRIVER_LABELS = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL / MariaDB",
    "sqlserver": "SQL Server",
    "oracle": "Oracle",
    "dameng": "达梦 DM8",
    "sqlite": "SQLite",
}

DEFAULT_PORTS = {
    "postgresql": 5432,
    "mysql": 3306,
    "sqlserver": 1433,
    "oracle": 1521,
    "dameng": 5236,
    "sqlite": "",
}


def quote_literal(v):
    """把字符串安全地拼进 SQL 字面量（单引号翻倍）。"""
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _import_first(names):
    """按顺序尝试导入，返回 (module, 实际用的名字, 尝试过的清单)。"""
    tried = []
    for n in names:
        try:
            __import__(n)
            mod = __import__(n)
            return mod, n, tried
        except Exception as e:
            tried.append("%s(%s)" % (n, type(e).__name__))
    return None, None, tried


def _to_str(v):
    """驱动返回值统一转字符串（达梦/部分驱动会返回非 str）。"""
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", "replace")
        except Exception:
            return repr(v)
    return str(v)


def _blank(s):
    """NULL / 空串 统一显示为空白。"""
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    if s.upper() in ("NULL", "N"):
        return "是"
    if s.upper() == "Y":
        return "否"
    return s


class Driver(object):
    """驱动基类。子类覆盖 SQL 与连接方式。"""

    name = ""
    label = ""
    default_port = 0
    modules = ()          # 候选模块名，按优先级
    sqlite = False

    def __init__(self):
        self._mod = None
        self._modname = None
        self._probed = False

    # ---- 可用性 ----
    def available(self):
        if self._probed:
            return (self._mod is not None, self._info())
        self._probed = True
        if self.sqlite:
            self._mod = sqlite3
            self._modname = "sqlite3(标准库)"
            return (True, self._info())
        mod, name, tried = _import_first(self.modules)
        self._mod = mod
        self._modname = name
        self._tried = tried
        return (mod is not None, self._info())

    def _info(self):
        if self._mod is None:
            return "未安装（尝试过: %s）" % ", ".join(getattr(self, "_tried", list(self.modules)))
        return "已就绪（%s）" % self._modname

    # ---- 连接 ----
    def connect(self, cfg, timeout=10, statement_timeout_ms=30000):
        ok, info = self.available()
        if not ok:
            raise RuntimeError("%s 驱动不可用：%s。%s" % (
                self.label, info, self._install_hint()))
        return self._do_connect(cfg, timeout, statement_timeout_ms)

    def _install_hint(self):
        return "请执行: pip install %s" % (self.modules[0] if self.modules else "")

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        raise NotImplementedError

    # ---- 元数据（默认 Oracle 风格，各驱动覆盖）----
    def schemas(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT username FROM all_users ORDER BY 1")
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT table_name FROM all_tables WHERE owner=%s ORDER BY 1"
                        % quote_literal(schema))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("SELECT column_name, data_type, nullable FROM all_tab_columns "
                        "WHERE owner=%s AND table_name=%s ORDER BY column_id"
                        % (quote_literal(schema), quote_literal(table)))
            rows = []
            for r in cur.fetchall():
                rows.append([_to_str(r[0]), _to_str(r[1]), _blank(r[2])])
            return rows
        finally:
            cur.close()

    def server_info(self, conn):
        return ""

    def overview(self, conn):
        """对象树顶层：schema 数量 + 默认 schema 的表数量。"""
        out = {"server": self.server_info(conn), "schemas": [], "table_count": 0}
        try:
            out["schemas"] = self.schemas(conn)
        except Exception as e:
            out["schema_error"] = "%s: %s" % (type(e).__name__, e)
        return out


# ---------------------------------------------------------------- PostgreSQL
class PostgresDriver(Driver):
    name = "postgresql"
    label = "PostgreSQL"
    default_port = 5432
    modules = ("psycopg2", "pg8000")

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        kw = dict(host=cfg["host"], port=int(cfg.get("port") or 5432),
                  user=cfg["user"], password=cfg.get("password") or "",
                  database=cfg.get("dbname") or "")
        if self._modname == "psycopg2":
            if timeout:
                kw["connect_timeout"] = int(timeout)
            if statement_timeout_ms:
                kw["options"] = "-c statement_timeout=%d" % int(statement_timeout_ms)
            return self._mod.connect(**kw)
        # pg8000：纯 Python，无 statement_timeout 入口
        kw.pop("database", None)
        kw["database"] = cfg.get("dbname") or ""
        return self._mod.connect(timeout=int(timeout or 10), **kw)

    def server_info(self, conn):
        return self._scalar(conn, "SELECT version()")

    def _scalar(self, conn, sql):
        cur = conn.cursor()
        try:
            cur.execute(sql)
            r = cur.fetchone()
            return _to_str(r[0]) if r else ""
        finally:
            cur.close()

    def schemas(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT nspname FROM pg_namespace "
                        "WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' "
                        "ORDER BY 1")
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY 1"
                        % quote_literal(schema))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position"
                        % (quote_literal(schema), quote_literal(table)))
            return [[_to_str(r[0]), _to_str(r[1]), _blank(r[2])] for r in cur.fetchall()]
        finally:
            cur.close()


# ---------------------------------------------------------------- MySQL
class MySQLDriver(Driver):
    name = "mysql"
    label = "MySQL / MariaDB"
    default_port = 3306
    modules = ("pymysql",)

    def _install_hint(self):
        return "请执行: pip install PyMySQL（纯 Python，或使用包内 wheels/ 目录离线安装）"

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        kw = dict(host=cfg["host"], port=int(cfg.get("port") or 3306),
                  user=cfg["user"], password=cfg.get("password") or "",
                  database=cfg.get("dbname") or None,
                  charset=cfg.get("charset") or "utf8mb4",
                  connect_timeout=int(timeout or 10))
        conn = self._mod.connect(**kw)
        if statement_timeout_ms:
            try:
                cur = conn.cursor()
                cur.execute("SET SESSION max_execution_time=%d" % int(statement_timeout_ms))
                cur.close()
            except Exception:
                pass
        return conn

    def server_info(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT VERSION()")
            r = cur.fetchone()
            return "MySQL " + _to_str(r[0]) if r else ""
        finally:
            cur.close()

    def schemas(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY 1")
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA=%s ORDER BY 1" % quote_literal(schema))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION"
                        % (quote_literal(schema), quote_literal(table)))
            return [[_to_str(r[0]), _to_str(r[1]), _blank(r[2])] for r in cur.fetchall()]
        finally:
            cur.close()


# ---------------------------------------------------------------- SQL Server
class SQLServerDriver(Driver):
    name = "sqlserver"
    label = "SQL Server"
    default_port = 1433
    modules = ("pymssql",)

    def _install_hint(self):
        return "请执行: pip install pymssql（需带 FreeTDS 的二进制 wheel，见 README）"

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        kw = dict(server=cfg["host"], port=str(int(cfg.get("port") or 1433)),
                  user=cfg["user"], password=cfg.get("password") or "",
                  database=cfg.get("dbname") or "",
                  timeout=int(timeout or 10),
                  login_timeout=int(timeout or 10))
        return self._mod.connect(**kw)

    def server_info(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT @@VERSION")
            r = cur.fetchone()
            return _to_str(r[0]).split("\n")[0] if r else ""
        finally:
            cur.close()

    def schemas(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT name FROM sys.databases ORDER BY 1")
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY 1"
                        % quote_literal(schema))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION"
                        % (quote_literal(schema), quote_literal(table)))
            return [[_to_str(r[0]), _to_str(r[1]), _blank(r[2])] for r in cur.fetchall()]
        finally:
            cur.close()


# ---------------------------------------------------------------- Oracle
class OracleDriver(Driver):
    name = "oracle"
    label = "Oracle"
    default_port = 1521
    modules = ("oracledb", "cx_Oracle")

    def _install_hint(self):
        return "请执行: pip install oracledb（thin 模式，无需安装 Oracle 客户端）"

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        svc = cfg.get("dbname") or cfg.get("service") or ""
        dsn = "%s:%s/%s" % (cfg["host"], int(cfg.get("port") or 1521), svc)
        kw = dict(user=cfg["user"], password=cfg.get("password") or "", dsn=dsn)
        try:
            if timeout:
                kw["tcp_connect_timeout"] = int(timeout)
        except Exception:
            pass
        return self._mod.connect(**kw)

    def server_info(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
            r = cur.fetchone()
            return _to_str(r[0]) if r else ""
        finally:
            cur.close()

    def schemas(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT username FROM all_users ORDER BY 1")
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT table_name FROM all_tables WHERE owner=%s ORDER BY 1"
                        % quote_literal(schema.upper()))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("SELECT column_name, data_type, nullable FROM all_tab_columns "
                        "WHERE owner=%s AND table_name=%s ORDER BY column_id"
                        % (quote_literal(schema.upper()), quote_literal(table.upper())))
            return [[_to_str(r[0]), _to_str(r[1]), _blank(r[2])] for r in cur.fetchall()]
        finally:
            cur.close()


# ---------------------------------------------------------------- 达梦 DM8
class DamengDriver(Driver):
    name = "dameng"
    label = "达梦 DM8"
    default_port = 5236
    modules = ("dmPython", "dmpy")

    def _install_hint(self):
        return ("Linux/Windows 用官方 dmPython（包内 wheels/ 已带）；"
                "macOS 官方无 wheel，会自动使用纯 Python 驱动 dmpy（pip install dm-pure）")

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        host = cfg["host"]
        port = int(cfg.get("port") or 5236)
        user = cfg["user"]
        pwd = cfg.get("password") or ""
        if self._modname == "dmPython":
            kw = dict(user=user, password=pwd, server=host, port=port,
                      autoCommit=bool(cfg.get("autocommit", True)))
            if cfg.get("dbname"):
                kw["schema"] = cfg["dbname"]
            try:
                return self._mod.connect(**kw)
            except TypeError:
                # 老版本不认 schema 关键字
                kw.pop("schema", None)
                return self._mod.connect(**kw)
        # dmpy（纯 Python）：达梦无"库"概念，schema 由 users 区分
        return self._mod.connect(host=host, port=port, user=user, password=pwd,
                                 timeout=int(timeout or 15), send_delay=0)

    def server_info(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM v$version")
            rows = cur.fetchall()
            return _to_str(rows[0][0]) if rows else ""
        finally:
            cur.close()

    def schemas(self, conn):
        """优先 DBA_USERS，退回 ALL_USERS；再退回库内 schema 对象。"""
        cur = conn.cursor()
        try:
            for sql in ("SELECT USERNAME FROM DBA_USERS ORDER BY 1",
                        "SELECT USERNAME FROM ALL_USERS ORDER BY 1",
                        "SELECT NAME FROM SYS.SYSOBJECTS WHERE TYPE$='SCH' ORDER BY 1"):
                try:
                    cur.execute(sql)
                    rows = [_to_str(r[0]) for r in cur.fetchall()]
                    if rows:
                        return rows
                except Exception:
                    continue
            return []
        finally:
            cur.close()


    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER=%s ORDER BY 1"
                        % quote_literal(schema.upper()))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("SELECT COLUMN_NAME, DATA_TYPE, NULLABLE FROM ALL_TAB_COLUMNS "
                        "WHERE OWNER=%s AND TABLE_NAME=%s ORDER BY COLUMN_ID"
                        % (quote_literal(schema.upper()), quote_literal(table.upper())))
            return [[_to_str(r[0]), _to_str(r[1]), _blank(r[2])] for r in cur.fetchall()]
        finally:
            cur.close()


# ---------------------------------------------------------------- SQLite
class SQLiteDriver(Driver):
    name = "sqlite"
    label = "SQLite"
    default_port = 0
    modules = ()
    sqlite = True

    def _do_connect(self, cfg, timeout, statement_timeout_ms):
        path = cfg.get("path") or cfg.get("dbname") or ""
        if not path:
            raise RuntimeError("SQLite 需要填写数据库文件路径（path）")
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        # 文件不存在时自动创建（SQLite 的惯例，与 DBeaver 等工具一致）；
        # 但父目录不存在一定报错，避免把路径拼错变成一个空库
        if not os.path.exists(path):
            parent = os.path.dirname(path)
            if not os.path.isdir(parent):
                raise RuntimeError("SQLite 目录不存在：%s" % parent)
        conn = sqlite3.connect(path, timeout=float(timeout or 10))
        conn.row_factory = None
        return conn

    def server_info(self, conn):
        return "SQLite " + _to_str(getattr(sqlite3, "sqlite_version", ""))

    def schemas(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA database_list")
            return [_to_str(r[1]) for r in cur.fetchall()]
        finally:
            cur.close()

    def tables(self, conn, schema):
        cur = conn.cursor()
        try:
            cur.execute("SELECT name FROM %s.sqlite_master WHERE type='table' ORDER BY 1"
                        % (_quote_ident(schema)))
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def columns(self, conn, schema, table):
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA %s.table_info(%s)" % (_quote_ident(schema), quote_literal(table)))
            rows = []
            for r in cur.fetchall():
                # cid, name, type, notnull, dflt_value, pk
                rows.append([_to_str(r[1]), _to_str(r[2]), "否" if r[3] else "是"])
            return rows
        finally:
            cur.close()


def _quote_ident(v):
    """SQLite 的 schema 名做标识符转义（不含引号）。"""
    return '"' + str(v).replace('"', '""') + '"'


# ---------------------------------------------------------------- 注册表
_REGISTRY = {
    "postgresql": PostgresDriver,
    "mysql": MySQLDriver,
    "sqlserver": SQLServerDriver,
    "oracle": OracleDriver,
    "dameng": DamengDriver,
    "sqlite": SQLiteDriver,
}

_instances = {}


def get_driver(name):
    """按名字取驱动单例。未知名字抛 ValueError。"""
    key = (name or "").strip().lower()
    if key not in _REGISTRY:
        raise ValueError("不支持的数据库类型: %s（可选: %s）"
                         % (name, ", ".join(DRIVER_NAMES)))
    if key not in _instances:
        _instances[key] = _REGISTRY[key]()
    return _instances[key]


def list_drivers():
    """返回所有支持的数据库类型及其驱动可用状态，供界面下拉框使用。"""
    out = []
    for n in DRIVER_NAMES:
        d = get_driver(n)
        ok, info = d.available()
        out.append({"name": n, "label": DRIVER_LABELS[n],
                    "default_port": DEFAULT_PORTS[n],
                    "available": ok, "info": info})
    return out

