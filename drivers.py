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
import sys

# 随包携带的纯 Python 驱动（vendor/ 解包版）：目标机可能没有 pip / venv / 外网，
# 甚至 Python 只有 3.7（pip 会因 pg8000、scramp、dm-pure 声明 Requires-Python>=3.8 而拒装）。
# 这些包本身是纯 Python 代码，解包后放进 sys.path 即可直接使用。
_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

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
    def databases(self, conn):
        """同一实例下的库清单。一个连接只绑一个库的驱动（Oracle/达梦/SQLite/MySQL）返回空。

        PostgreSQL、SQL Server 这类"多库实例"返回全部库名，前端据此多渲染一层；
        不用本方法切换连接本身，只用于导航——真正跨库取数由上层把 dbname 换掉重连。
        """
        return []

    def current_database(self, conn):
        """当前连接实际落在哪个库。dbname 留空时（PG 默认连与用户名同名的库）靠它对齐。"""
        return ""

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
        """对象树顶层：库清单 + schema 数量。"""
        out = {"server": self.server_info(conn), "databases": [], "schemas": [],
               "table_count": 0, "current_database": ""}
        try:
            out["databases"] = self.databases(conn)
        except Exception as e:
            out["database_error"] = "%s: %s" % (type(e).__name__, e)
        try:
            out["current_database"] = self.current_database(conn) or ""
        except Exception:
            pass
        try:
            out["schemas"] = self.schemas(conn)
        except Exception as e:
            out["schema_error"] = "%s: %s" % (type(e).__name__, e)
        return out

    def schema_table_counts(self, conn):
        """每个模式有多少张表：{模式名: 数量}。

        对象树里 schema 行右侧要显示表数（如 public 73），必须一次查询算全部；
        逐个模式去数就是几十次往返。默认返回空字典（不显示数量、不报错），
        由各驱动覆盖成自己的批量统计 SQL。
        """
        return {}


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

    def current_database(self, conn):
        return self._scalar(conn, "SELECT current_database()")

    def _scalar(self, conn, sql):
        cur = conn.cursor()
        try:
            cur.execute(sql)
            r = cur.fetchone()
            return _to_str(r[0]) if r else ""
        finally:
            cur.close()

    def databases(self, conn):
        # PostgreSQL 一个连接只绑一个库，pg_namespace 只能看到当前库的 schema，
        # 所以整实例的库清单必须单独从 pg_database 取
        return self._scalar_list(conn, "SELECT datname FROM pg_database "
                                 "WHERE datistemplate = false ORDER BY 1")

    def _scalar_list(self, conn, sql):
        cur = conn.cursor()
        try:
            cur.execute(sql)
            return [_to_str(r[0]) for r in cur.fetchall()]
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

    def schema_table_counts(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT schemaname, count(*) FROM pg_tables GROUP BY 1")
            return {_to_str(r[0]): int(r[1]) for r in cur.fetchall()}
        except Exception:
            return {}
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

    def current_database(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT DATABASE()")
            r = cur.fetchone()
            return _to_str(r[0]) if r and r[0] else ""
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

    def schema_table_counts(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT TABLE_SCHEMA, count(*) FROM information_schema.TABLES GROUP BY 1")
            return {_to_str(r[0]): int(r[1]) for r in cur.fetchall()}
        except Exception:
            return {}
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

    def databases(self, conn):
        return self._scalar_list(conn, "SELECT name FROM sys.databases ORDER BY 1")

    def current_database(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT DB_NAME()")
            r = cur.fetchone()
            return _to_str(r[0]) if r else ""
        finally:
            cur.close()

    def _scalar_list(self, conn, sql):
        cur = conn.cursor()
        try:
            cur.execute(sql)
            return [_to_str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    def schemas(self, conn):
        # SQL Server 的 schema 属于当前库（dbo/guest…），跨库导航由 databases() + 换 dbname 完成。
        # 原来这里返回的是 sys.databases，属于把库当 schema，已分开。
        cur = conn.cursor()
        try:
            cur.execute("SELECT name FROM sys.schemas "
                        "WHERE schema_id < 16384 AND name NOT IN ('sys','INFORMATION_SCHEMA') "
                        "ORDER BY 1")
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

    def schema_table_counts(self, conn):
        # SQL Server 的 schema 属于当前库，统计也只在当前库内
        cur = conn.cursor()
        try:
            cur.execute("SELECT s.name, COUNT(*) FROM sys.tables t "
                        "JOIN sys.schemas s ON s.schema_id = t.schema_id GROUP BY s.name")
            return {_to_str(r[0]): int(r[1]) for r in cur.fetchall()}
        except Exception:
            return {}
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

    def schema_table_counts(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT owner, count(*) FROM all_tables GROUP BY owner")
            return {_to_str(r[0]): int(r[1]) for r in cur.fetchall()}
        except Exception:
            return {}
        finally:
            cur.close()
# ---------------------------------------------------------------- 达梦 DM8
class DamengDriver(Driver):
    name = "dameng"
    label = "达梦 DM8"
    default_port = 5236
    modules = ("dmPython", "dmpy")
    # 达梦的列结构能给出类型长度、默认值和键标记，界面上按 5 列展示
    columns_head = ("列名", "类型", "可空", "默认值", "键")

    def _install_hint(self):
        return ("包内 wheels/ 带的是纯 Python 驱动 dm-pure（导入名 dmpy），"
                "Windows 跑 install.bat -InstallDeps、Linux/macOS 跑 ./install.sh --install-deps 装上即可；"
                "要用达梦官方 dmPython 二进制驱动，需按本机 Python 版本 pip download 后放进 wheels/ 再装")

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
        """三源合并：DBA_USERS / ALL_USERS（登录名） + SYSOBJECTS 里的独立模式。

        达梦的 CREATE SCHEMA 只建模式对象、不建用户，所以只查 DBA_USERS 会漏掉
        「独立模式」——比如用 SYSDBA 建的 SQLWEB_DMTEST 在 DBA_USERS 里根本没有，
        树里就整块消失。系统模式排在业务模式之后，免得一进树先看到一堆 SYS*。
        """
        cur = conn.cursor()
        out, seen = [], set()
        try:
            for sql in ("SELECT USERNAME FROM DBA_USERS ORDER BY 1",
                        "SELECT USERNAME FROM ALL_USERS ORDER BY 1",
                        "SELECT NAME FROM SYS.SYSOBJECTS WHERE TYPE$='SCH' ORDER BY 1"):
                try:
                    cur.execute(sql)
                    rows = [_to_str(r[0]) for r in cur.fetchall()]
                except Exception:
                    continue
                for r in rows:
                    if r and r not in seen:
                        seen.add(r)
                        out.append(r)
            return sorted(out, key=lambda s: (_dm_sys_schema(s), s.upper()))
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
        # 达梦：类型带长度、可空、默认值、主键/唯一标记（见模块内 _dm_column_list）
        return _dm_column_list(conn, schema, table)

    def schema_table_counts(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT owner, count(*) FROM ALL_TABLES GROUP BY owner")
            return {_to_str(r[0]): int(r[1]) for r in cur.fetchall()}
        except Exception:
            return {}
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

    def schema_table_counts(self, conn):
        out = {}
        for s in self.schemas(conn):
            try:
                out[s] = len(self.tables(conn, s))
            except Exception:
                continue
        return out


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


# ---------------------------------------------------------------- 对象树 / 定义 / 补全字典
# 各驱动按自己的系统视图取「表之外的对象」：视图、存储过程、函数、序列、触发器。
# 取不到就返回空，宁可少列也不让整棵树挂掉。

_KIND_KEYS = ("table", "view", "procedure", "function", "sequence", "trigger", "synonym")


def _empty_objects():
    return dict((k, []) for k in _KIND_KEYS)


def _q(conn, sql):
    """跑一条只读查询，顺手把游标关掉。"""
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _ident_backtick(s):
    return "`" + str(s).replace("`", "``") + "`"


def _ident_bracket(s):
    return "[" + str(s).replace("]", "]]") + "]"


# ---- PostgreSQL ----
_PG_RELKIND = {"r": "table", "p": "table", "f": "table", "v": "view", "m": "view", "S": "sequence"}


def _objects_pg(conn, schema):
    out = _empty_objects()
    for name, kind in _q(conn,
                         "SELECT c.relname, c.relkind FROM pg_class c "
                         "JOIN pg_namespace n ON n.oid = c.relnamespace "
                         "WHERE n.nspname = %s AND c.relkind IN ('r','p','f','v','m','S') "
                         "ORDER BY 1" % quote_literal(schema)):
        k = _PG_RELKIND.get(_to_str(kind))
        if k:
            out[k].append(_to_str(name))
    try:
        rows = _q(conn, "SELECT p.proname, p.prokind FROM pg_proc p "
                        "JOIN pg_namespace n ON n.oid = p.pronamespace "
                        "WHERE n.nspname = %s ORDER BY 1" % quote_literal(schema))
        for name, pk in rows:
            out["procedure" if _to_str(pk) == "p" else "function"].append(_to_str(name))
    except Exception:
        # PG 10 及更早没有 prokind
        for (name,) in _q(conn, "SELECT p.proname FROM pg_proc p "
                                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                                "WHERE n.nspname = %s ORDER BY 1" % quote_literal(schema)):
            out["function"].append(_to_str(name))
    try:
        # 触发器挂在 pg_trigger 上（不在 pg_class），必须先按 tgrelid 找到所属表再取 tgname
        for name, _tbl in _q(conn,
                             "SELECT t.tgname, c.relname FROM pg_trigger t "
                             "JOIN pg_class c ON c.oid = t.tgrelid "
                             "JOIN pg_namespace n ON n.oid = c.relnamespace "
                             "WHERE n.nspname = %s AND NOT t.tgisinternal "
                             "ORDER BY 1" % quote_literal(schema)):
            out["trigger"].append(_to_str(name))
    except Exception:
        pass
    return out


def _source_pg(conn, schema, name, kind):
    if kind == "trigger":
        # pg_get_triggerdef 直接给完整 CREATE TRIGGER，比自己拼 tgtype 位可靠
        rows = _q(conn, "SELECT pg_get_triggerdef(t.oid, true) FROM pg_trigger t "
                        "JOIN pg_class c ON c.oid = t.tgrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = %s AND t.tgname = %s AND NOT t.tgisinternal "
                        "ORDER BY t.oid DESC"
                        % (quote_literal(schema), quote_literal(name)))
        if not rows:
            return ""
        txt = _to_str(rows[0][0]).strip()
        return txt if txt.endswith(";") else txt + ";"
    if kind == "sequence":
        try:
            rows = _q(conn, "SELECT s.seqstart, s.seqincrement, s.seqmin, s.seqmax, "
                            "s.seqcache, s.seqcycle FROM pg_sequence s "
                            "JOIN pg_class c ON c.oid = s.seqrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = %s AND c.relname = %s"
                            % (quote_literal(schema), quote_literal(name)))
        except Exception:
            # PG 9.x 没有 pg_sequence 视图
            rows = []
        if not rows:
            return ""
        st, inc, mn, mx, ca, cyc = [_to_str(v) for v in rows[0]]
        return ('CREATE SEQUENCE "%s"."%s"\n'
                '    START WITH %s\n    INCREMENT BY %s\n'
                '    MINVALUE %s\n    MAXVALUE %s\n    CACHE %s%s;'
                % (schema, name, st, inc, mn, mx, ca, "\n    CYCLE" if ca and cyc in ("t", "true", "1") else ""))
    if kind == "view":
        rows = _q(conn, "SELECT c.relkind, pg_get_viewdef(c.oid, true) FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = %s AND c.relname = %s"
                        % (quote_literal(schema), quote_literal(name)))
        if not rows:
            return ""
        rk = _to_str(rows[0][0])
        head = "CREATE MATERIALIZED VIEW" if rk == "m" else "CREATE OR REPLACE VIEW"
        return '%s "%s"."%s" AS\n%s;' % (head, schema, name, _to_str(rows[0][1]))
    rows = _q(conn, "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = %s AND p.proname = %s ORDER BY p.oid DESC"
                    % (quote_literal(schema), quote_literal(name)))
    return _to_str(rows[0][0]) if rows else ""


def _catalog_pg(conn, schema, limit=400):
    rows = _q(conn, "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
                    "WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m') "
                    "ORDER BY c.relname, a.attnum" % quote_literal(schema))
    return rows


# ---- MySQL ----
def _objects_mysql(conn, schema):
    out = _empty_objects()
    for name, t in _q(conn, "SELECT table_name, table_type FROM information_schema.tables "
                            "WHERE table_schema = %s ORDER BY 1" % quote_literal(schema)):
        out["view" if "VIEW" in _to_str(t).upper() else "table"].append(_to_str(name))
    try:
        for name, t in _q(conn, "SELECT routine_name, routine_type FROM information_schema.routines "
                                "WHERE routine_schema = %s ORDER BY 1" % quote_literal(schema)):
            out["procedure" if _to_str(t).upper() == "PROCEDURE" else "function"].append(_to_str(name))
    except Exception:
        pass
    try:
        for (name,) in _q(conn, "SELECT trigger_name FROM information_schema.triggers "
                                "WHERE trigger_schema = %s ORDER BY 1" % quote_literal(schema)):
            out["trigger"].append(_to_str(name))
    except Exception:
        pass
    return out


def _source_mysql(conn, schema, name, kind):
    if kind == "view":
        rows = _q(conn, "SHOW CREATE VIEW %s.%s" % (_ident_backtick(schema), _ident_backtick(name)))
        return _to_str(rows[0][1]) if rows and len(rows[0]) > 1 else ""
    if kind == "trigger":
        rows = _q(conn, "SHOW CREATE TRIGGER %s" % _ident_backtick(name))
        return _to_str(rows[0][2]) if rows and len(rows[0]) > 2 else ""
    rows = _q(conn, "SELECT routine_definition FROM information_schema.routines "
                    "WHERE routine_schema = %s AND routine_name = %s"
                    % (quote_literal(schema), quote_literal(name)))
    if not rows or not rows[0][0]:
        return ""
    head = "CREATE %s %s.%s\n" % (kind.upper(), schema, name)
    return head + _to_str(rows[0][0])


def _catalog_mysql(conn, schema, limit=400):
    return _q(conn, "SELECT table_name, column_name, column_type FROM information_schema.columns "
                    "WHERE table_schema = %s ORDER BY table_name, ordinal_position" % quote_literal(schema))


# ---- SQL Server ----
_MSSQL_TYPE = {"U": "table", "V": "view", "P": "procedure", "FN": "function", "IF": "function",
               "TF": "function", "FS": "function", "FT": "function", "SO": "sequence",
               "TR": "trigger", "SN": "synonym"}


def _objects_mssql(conn, schema):
    out = _empty_objects()
    for name, t in _q(conn, "SELECT o.name, RTRIM(o.type) FROM sys.objects o "
                            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
                            "WHERE s.name = %s ORDER BY 1" % quote_literal(schema)):
        k = _MSSQL_TYPE.get(_to_str(t).strip().upper())
        if k:
            out[k].append(_to_str(name))
    return out


def _source_mssql(conn, schema, name, kind):
    rows = _q(conn, "SELECT TOP 1 m.definition FROM sys.sql_modules m "
                    "JOIN sys.objects o ON o.object_id = m.object_id "
                    "JOIN sys.schemas s ON s.schema_id = o.schema_id "
                    "WHERE s.name = %s AND o.name = %s"
                    % (quote_literal(schema), quote_literal(name)))
    return _to_str(rows[0][0]) if rows and rows[0][0] else ""


def _catalog_mssql(conn, schema, limit=400):
    return _q(conn, "SELECT o.name, c.name, t.name FROM sys.objects o "
                    "JOIN sys.schemas s ON s.schema_id = o.schema_id "
                    "JOIN sys.columns c ON c.object_id = o.object_id "
                    "LEFT JOIN sys.types t ON t.user_type_id = c.user_type_id "
                    "WHERE s.name = %s AND o.type IN ('U','V') ORDER BY o.name, c.column_id"
                    % quote_literal(schema))


# ---- Oracle / 达梦（ALL_* 兼容视图）----
_ORA_TYPE = {"TABLE": "table", "VIEW": "view", "PROCEDURE": "procedure", "FUNCTION": "function",
             "SEQUENCE": "sequence", "TRIGGER": "trigger", "SYNONYM": "synonym"}


def _objects_ora(conn, schema):
    out = _empty_objects()
    try:
        for name, t in _q(conn, "SELECT object_name, object_type FROM all_objects "
                                "WHERE owner = %s AND object_type IN "
                                "('TABLE','VIEW','PROCEDURE','FUNCTION','SEQUENCE','TRIGGER') "
                                "ORDER BY 1" % quote_literal(schema)):
            k = _ORA_TYPE.get(_to_str(t).upper())
            if k:
                out[k].append(_to_str(name))
    except Exception:
        pass
    if not out["table"]:
        try:
            out["table"] = [_to_str(r[0]) for r in
                            _q(conn, "SELECT table_name FROM all_tables WHERE owner = %s ORDER BY 1"
                                     % quote_literal(schema))]
        except Exception:
            pass
    if not out["view"]:
        try:
            out["view"] = [_to_str(r[0]) for r in
                           _q(conn, "SELECT view_name FROM all_views WHERE owner = %s ORDER BY 1"
                                    % quote_literal(schema))]
        except Exception:
            pass
    return out


def _source_ora(conn, schema, name, kind):
    if kind == "view":
        try:
            rows = _q(conn, "SELECT text FROM all_views WHERE owner = %s AND view_name = %s"
                            % (quote_literal(schema), quote_literal(name)))
            if rows:
                return "CREATE OR REPLACE VIEW \"%s\".\"%s\" AS\n%s" % (schema, name, _to_str(rows[0][0]))
        except Exception:
            pass
        return ""
    try:
        rows = _q(conn, "SELECT text FROM all_source WHERE owner = %s AND name = %s AND type = %s "
                        "ORDER BY line" % (quote_literal(schema), quote_literal(name),
                                           quote_literal(kind.upper())))
        return "".join(_to_str(r[0]) for r in rows)
    except Exception:
        return ""


def _catalog_ora(conn, schema, limit=400):
    return _q(conn, "SELECT table_name, column_name, data_type FROM all_tab_columns "
                    "WHERE owner = %s ORDER BY table_name, column_id" % quote_literal(schema))


# ---- 达梦 DM8（以下实现全部基于 DM8 8.4 企业版实机核对）----
# 实测结论（业务账号 EALEPM / EALCONFIG，非 DBA）：
#   1) SYS.SYSOBJECTS / SYS.SYSCOLUMNS / SYS.SYSTEXTS / SYS.SYSCOLUMNCOMMENTS 一律 -5504 无权限，
#      只能用 ALL_* 兼容视图；凡是 SYS 路径都放在 try 里，DBA 数据源才生效；
#   2) ALL_OBJECTS 能返回 TABLE/VIEW/PROCEDURE/FUNCTION/SEQUENCE/TRIGGER/PACKAGE，
#      且天然只含"有权限"的对象，所以左侧不会出现无权访问的对象；
#   3) 触发器源码在 ALL_SOURCE 里 TYPE='TRIG'（不是 TRIGGER），整段单行返回；
#   4) ALL_SEQUENCES 的属主列是 SEQUENCE_OWNER，ALL_IND_COLUMNS 的是 INDEX_OWNER，
#      直接用 OWNER 会报"无效的列名"；
#   5) ALL_TAB_COLUMNS 没有 IDENTITY_COLUMN：自增只能读 SYS.SYSCOLUMNS.INFO2 的 BIT(0)（需 DBA）；
#      列注释在 ALL_COL_COMMENTS 里取不到，只在 SYS.SYSCOLUMNCOMMENTS（需 DBA）；
#   6) TABLEDEF() 与 DBMS_METADATA.GET_DDL() 在该版本都返回空，表 DDL 只能按字典拼装。
_DM_KIND = {"TABLE": "table", "VIEW": "view", "PROCEDURE": "procedure",
            "FUNCTION": "function", "SEQUENCE": "sequence", "TRIGGER": "trigger"}
_DM_SRC_TYPE = {"procedure": "PROCEDURE", "function": "FUNCTION",
                "trigger": "TRIG", "package": "PACKAGE"}
_DM_CHAR_TYPES = ("VARCHAR", "VARCHAR2", "CHAR", "NVARCHAR", "NCHAR", "VARBINARY", "BINARY")
_DM_NUM_TYPES = ("DECIMAL", "NUMERIC", "NUMBER")
_DM_SYS_SCHEMAS = {"SYS", "SYSDBA", "SYSAUDITOR", "SYSSSO", "CTISYS", "SYSJOB",
                   "GUEST", "SYSTEM", "SYSDBA_JOB", "BAKER"}


def _dm_up(v):
    return _to_str(v).upper()


def _dm_int(v):
    try:
        return int(v)
    except Exception:
        return 0


def _dm_sys_schema(name):
    """系统自带的模式：排序时压到业务模式后面。"""
    return 0 if _dm_up(name) not in _DM_SYS_SCHEMAS else 1


def _dm_nullable(v):
    """ALL_TAB_COLUMNS.NULLABLE：Y 可空 / N 不可空。
    注意不能用通用 _blank()：它把 N 当成"是"、Y 当成"否"，对达梦正好反了，
    而且会把默认值里的字符串 'N'/'NULL' 也误改成中文。"""
    k = _dm_up(v)
    if k == "Y":
        return "是"
    if k == "N":
        return "否"
    return _to_str(v)


def _dm_type_text(dt, length, prec, scale, clen, cused):
    """把 ALL_TAB_COLUMNS 的类型字段还原成 DDL 写法。
    实测：VARCHAR(50 CHAR) 存成 DATA_LENGTH=200 / CHAR_LENGTH=50 / CHAR_USED='C'；
    NUMBER(22) 的 DATA_PRECISION 与 DATA_SCALE 都是空；DECIMAL(12,2) 两者都有值。"""
    t = _dm_up(dt)
    if not t:
        return ""
    if t in _DM_CHAR_TYPES:
        n = _dm_int(clen) or _dm_int(length)
        if not n:
            return t
        return "%s(%d CHAR)" % (t, n) if _dm_up(cused) == "C" else "%s(%d)" % (t, n)
    if t in _DM_NUM_TYPES:
        p = _dm_int(prec) if prec is not None else 0
        s = _dm_int(scale) if scale is not None else None
        if not p:
            return t
        return "%s(%d,%d)" % (t, p, s or 0) if s is not None else "%s(%d)" % (t, p)
    if t in ("TIMESTAMP", "TIME"):
        sc = _dm_int(scale) if scale is not None else -1
        return "%s(%d)" % (t, sc) if 0 <= sc <= 9 else t
    return t


def _dm_identity_cols(conn, schema, table):
    """自增列集合（列名）。达梦只在 SYS.SYSCOLUMNS.INFO2 的 BIT(0) 标记，需要 DBA 权限。"""
    out = set()
    try:
        rows = _q(conn, "SELECT COLID, INFO2 FROM SYS.SYSCOLUMNS WHERE ID=("
                        "SELECT ID FROM SYS.SYSOBJECTS WHERE NAME=%s AND SCHID=("
                        "SELECT ID FROM SYS.SYSOBJECTS WHERE NAME=%s AND TYPE$='SCH'))"
                        % (quote_literal(_dm_up(table)), quote_literal(_dm_up(schema))))
        for colid, info2 in rows:
            if _dm_int(info2) & 1:
                out.add(_dm_int(colid) + 1)      # SYSCOLUMNS 的 COLID 从 0 起，ALL_TAB_COLUMNS 的 COLUMN_ID 从 1 起
    except Exception:
        pass
    return out


def _dm_key_cols(conn, schema, table):
    """列名 -> ['PK'] / ['UK']，供列结构面板展示键标记。"""
    out = {}
    s, t = _dm_up(schema), _dm_up(table)
    try:
        cons = _q(conn, "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM ALL_CONSTRAINTS "
                        "WHERE OWNER=%s AND TABLE_NAME=%s AND CONSTRAINT_TYPE IN ('P','U')"
                        % (quote_literal(s), quote_literal(t)))
        names = dict((_to_str(r[0]), _to_str(r[1])) for r in cons)
        if names:
            for cn, col in _q(conn, "SELECT CONSTRAINT_NAME, COLUMN_NAME FROM ALL_CONS_COLUMNS "
                                    "WHERE OWNER=%s AND TABLE_NAME=%s ORDER BY CONSTRAINT_NAME, POSITION"
                                    % (quote_literal(s), quote_literal(t))):
                ctype = names.get(_to_str(cn))
                if ctype:
                    out.setdefault(_to_str(col), []).append("PK" if ctype == "P" else "UK")
    except Exception:
        pass
    return out


def _dm_column_list(conn, schema, table):
    """列结构（增强版）：列名 / 类型(含长度) / 可空 / 默认值 / 键。"""
    s, t = _dm_up(schema), _dm_up(table)
    rows = _q(conn, "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, "
                    "CHAR_LENGTH, CHAR_USED, NULLABLE, DATA_DEFAULT FROM ALL_TAB_COLUMNS "
                    "WHERE OWNER=%s AND TABLE_NAME=%s ORDER BY COLUMN_ID"
                    % (quote_literal(s), quote_literal(t)))
    keys = _dm_key_cols(conn, s, t)
    return [[_to_str(r[0]), _dm_type_text(r[1], r[2], r[3], r[4], r[5], r[6]),
             _dm_nullable(r[7]), _to_str(r[8]), "/".join(keys.get(_to_str(r[0]), []))]
            for r in rows]


def _dm_column_comments(conn, schema, table):
    """列注释：ALL_COL_COMMENTS 在达梦取不到值，退回 SYS.SYSCOLUMNCOMMENTS（DBA 才行）。"""
    out = {}
    try:
        rows = _q(conn, "SELECT COLNAME, COMMENT$ FROM SYS.SYSCOLUMNCOMMENTS "
                        "WHERE SCHNAME=%s AND TVNAME=%s"
                        % (quote_literal(_dm_up(schema)), quote_literal(_dm_up(table))))
        for c, memo in rows:
            if _to_str(memo):
                out[_to_str(c)] = _to_str(memo)
    except Exception:
        pass
    return out


def _dm_ident_list(cols):
    return ", ".join('"%s"' % c for c in cols)


def _dm_table_ddl(conn, schema, table):
    """按字典拼出建表语句（达梦没有可用的 TABLEDEF/GET_DDL）。
    列 + 主键/唯一/外键 + 普通索引 + 表/列注释；自增列在 DBA 数据源下才还原。"""
    s, t = _dm_up(schema), _dm_up(table)
    try:
        cols = _q(conn, "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, "
                        "CHAR_LENGTH, CHAR_USED, NULLABLE, DATA_DEFAULT, COLUMN_ID FROM ALL_TAB_COLUMNS "
                        "WHERE OWNER=%s AND TABLE_NAME=%s ORDER BY COLUMN_ID"
                        % (quote_literal(s), quote_literal(t)))
    except Exception:
        cols = []
    if not cols:
        return ""
    ident = _dm_identity_cols(conn, s, t)

    # 约束（PK/UK/FK）
    pk_name, pk_cols, uk_items, fk_items, cons_idx = None, [], [], [], set()
    try:
        rows = _q(conn, "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE, R_OWNER, R_CONSTRAINT_NAME, INDEX_NAME "
                        "FROM ALL_CONSTRAINTS WHERE OWNER=%s AND TABLE_NAME=%s "
                        "AND CONSTRAINT_TYPE IN ('P','U','R') ORDER BY CONSTRAINT_TYPE, CONSTRAINT_NAME"
                        % (quote_literal(s), quote_literal(t)))
    except Exception:
        rows = []
    cons_cols = {}
    if rows:
        try:
            for cn, col in _q(conn, "SELECT CONSTRAINT_NAME, COLUMN_NAME FROM ALL_CONS_COLUMNS "
                                    "WHERE OWNER=%s AND TABLE_NAME=%s ORDER BY CONSTRAINT_NAME, POSITION"
                                    % (quote_literal(s), quote_literal(t))):
                cons_cols.setdefault(_to_str(cn), []).append(_to_str(col))
        except Exception:
            pass
    for cn, ctype, rowner, rcons, iname in rows:
        cn, ctype = _to_str(cn), _to_str(ctype)
        if iname:
            cons_idx.add(_to_str(iname))
        if ctype == "P":
            pk_name, pk_cols = cn, cons_cols.get(cn, [])
        elif ctype == "U":
            uk_items.append((cn, cons_cols.get(cn, [])))
        elif ctype == "R":
            ref_t, ref_c, ref_name = "", [], ""
            try:
                rr = _q(conn, "SELECT TABLE_NAME FROM ALL_CONSTRAINTS WHERE OWNER=%s AND CONSTRAINT_NAME=%s"
                        % (quote_literal(_dm_up(rowner) or s), quote_literal(_to_str(rcons))))
                ref_t = _to_str(rr[0][0]) if rr else ""
                if ref_t:
                    rc = _q(conn, "SELECT COLUMN_NAME FROM ALL_CONS_COLUMNS WHERE OWNER=%s AND TABLE_NAME=%s "
                                  "AND CONSTRAINT_NAME=%s ORDER BY POSITION"
                            % (quote_literal(_dm_up(rowner) or s), quote_literal(ref_t),
                               quote_literal(_to_str(rcons))))
                    ref_c = [_to_str(x[0]) for x in rc]
            except Exception:
                pass
            fk_items.append((cn, cons_cols.get(cn, []), ref_t, ref_c))

    lines = []
    for r in cols:
        name = _to_str(r[0])
        typ = _dm_type_text(r[1], r[2], r[3], r[4], r[5], r[6])
        if _dm_int(r[9]) in ident:
            typ += " IDENTITY(1,1)"
        line = '    "%s" %s' % (name, typ)
        if _dm_up(r[7]) == "N":
            line += " NOT NULL"
        if _to_str(r[8]):
            line += " DEFAULT %s" % _to_str(r[8])
        lines.append(line)
    if pk_cols:
        lines.append('    CONSTRAINT "%s" PRIMARY KEY(%s)' % (pk_name, _dm_ident_list(pk_cols)))
    for cn, c in uk_items:
        if c:
            lines.append('    CONSTRAINT "%s" UNIQUE(%s)' % (cn, _dm_ident_list(c)))
    for cn, c, ref_t, ref_c in fk_items:
        if c and ref_t:
            # 外键必须带模式名：达梦对不带模式名的表按当前会话模式解析，换个账号执行就会找不到表
            ref = '"%s"."%s"' % (_dm_up(rowner) or s, ref_t)
            if ref_c:
                ref += "(%s)" % _dm_ident_list(ref_c)
            lines.append('    CONSTRAINT "%s" FOREIGN KEY(%s) REFERENCES %s' % (cn, _dm_ident_list(c), ref))

    ddl = 'CREATE TABLE "%s"."%s" (\n%s\n);' % (s, t, ",\n".join(lines))

    # 普通索引（跳过约束自带的系统索引，INDEX3355xxxx 是达梦自动生成的约束/聚簇索引）
    try:
        idx = _q(conn, "SELECT INDEX_NAME, UNIQUENESS, INDEX_TYPE FROM ALL_INDEXES "
                       "WHERE OWNER=%s AND TABLE_NAME=%s ORDER BY INDEX_NAME"
                       % (quote_literal(s), quote_literal(t)))
        idx_cols = {}
        if idx:
            for iname, cname, pos, desc in _q(conn, "SELECT INDEX_NAME, COLUMN_NAME, COLUMN_POSITION, DESCEND "
                                                    "FROM ALL_IND_COLUMNS WHERE INDEX_OWNER=%s AND TABLE_NAME=%s "
                                                    "ORDER BY INDEX_NAME, COLUMN_POSITION"
                                                    % (quote_literal(s), quote_literal(t))):
                idx_cols.setdefault(_to_str(iname), []).append(_to_str(cname))
        for iname, uniq, itype in idx:
            iname, itype = _to_str(iname), _dm_up(itype)
            if iname in cons_idx or itype in ("CLUSTER", "VIRTUAL") or not idx_cols.get(iname):
                continue
            ddl += "\nCREATE %sINDEX \"%s\" ON \"%s\".\"%s\"(%s);" % (
                "UNIQUE " if _dm_up(uniq) == "UNIQUE" else "", iname, s, t,
                _dm_ident_list(idx_cols[iname]))
    except Exception:
        pass

    # 注释
    try:
        memo = _q(conn, "SELECT COMMENTS FROM ALL_TAB_COMMENTS WHERE OWNER=%s AND TABLE_NAME=%s"
                 % (quote_literal(s), quote_literal(t)))
        if memo and _to_str(memo[0][0]):
            ddl += '\nCOMMENT ON TABLE "%s"."%s" IS %s;' % (s, t, quote_literal(_to_str(memo[0][0])))
    except Exception:
        pass
    for cname, memo in sorted(_dm_column_comments(conn, s, t).items()):
        ddl += '\nCOMMENT ON COLUMN "%s"."%s"."%s" IS %s;' % (s, t, cname, quote_literal(memo))
    return ddl


def _dm_sequence_ddl(conn, schema, name):
    s, n = _dm_up(schema), _dm_up(name)
    try:
        rows = _q(conn, "SELECT MIN_VALUE, MAX_VALUE, INCREMENT_BY, CYCLE_FLAG, CACHE_SIZE, LAST_NUMBER "
                        "FROM ALL_SEQUENCES WHERE SEQUENCE_OWNER=%s AND SEQUENCE_NAME=%s"
                        % (quote_literal(s), quote_literal(n)))
    except Exception:
        rows = []
    if not rows:
        return ""
    r = rows[0]
    parts = ['CREATE SEQUENCE "%s"."%s"' % (s, n)]
    if _to_str(r[2]):
        parts.append("INCREMENT BY %s" % _to_str(r[2]))
    if _to_str(r[0]):
        parts.append("MINVALUE %s" % _to_str(r[0]))
    if _to_str(r[1]):
        parts.append("MAXVALUE %s" % _to_str(r[1]))
    parts.append("CYCLE" if _dm_up(r[3]) == "Y" else "NOCYCLE")
    cache = _to_str(r[4])
    parts.append("CACHE %s" % cache if cache and cache != "0" else "NOCACHE")
    if _to_str(r[5]):
        parts.append("START WITH %s" % _to_str(r[5]))
    return " ".join(parts) + ";"


def _objects_dm(conn, schema):
    out = _empty_objects()
    s = _dm_up(schema)
    try:
        for name, t in _q(conn, "SELECT OBJECT_NAME, OBJECT_TYPE FROM ALL_OBJECTS "
                                "WHERE OWNER=%s AND SUBOBJECT_NAME IS NULL AND OBJECT_TYPE IN "
                                "('TABLE','VIEW','PROCEDURE','FUNCTION','SEQUENCE','TRIGGER') ORDER BY 1"
                                % quote_literal(s)):
            k = _DM_KIND.get(_dm_up(t))
            if k:
                out[k].append(_to_str(name))
    except Exception:
        pass
    # 兜底：某些版本/权限下 ALL_OBJECTS 不含视图、序列、触发器
    for kind, sql in (
            ("view", "SELECT VIEW_NAME FROM ALL_VIEWS WHERE OWNER=%s ORDER BY 1"),
            ("sequence", "SELECT SEQUENCE_NAME FROM ALL_SEQUENCES WHERE SEQUENCE_OWNER=%s ORDER BY 1"),
            ("trigger", "SELECT TRIGGER_NAME FROM ALL_TRIGGERS WHERE OWNER=%s ORDER BY 1")):
        if not out[kind]:
            try:
                out[kind] = [_to_str(r[0]) for r in _q(conn, sql % quote_literal(s))]
            except Exception:
                pass
    return out


def _source_dm(conn, schema, name, kind):
    s, n = _dm_up(schema), _dm_up(name)
    k = (kind or "").lower()
    if k == "table":
        return _dm_table_ddl(conn, s, n)
    if k == "sequence":
        return _dm_sequence_ddl(conn, s, n)
    if k == "view":
        try:
            rows = _q(conn, "SELECT TEXT FROM ALL_VIEWS WHERE OWNER=%s AND VIEW_NAME=%s"
                            % (quote_literal(s), quote_literal(n)))
            if rows and _to_str(rows[0][0]):
                return 'CREATE OR REPLACE VIEW "%s"."%s" AS\n%s' % (s, n, _to_str(rows[0][0]))
        except Exception:
            pass
        return ""
    stype = _DM_SRC_TYPE.get(k)
    if not stype:
        return ""
    try:
        rows = _q(conn, "SELECT TEXT FROM ALL_SOURCE WHERE OWNER=%s AND NAME=%s AND TYPE=%s ORDER BY LINE"
                        % (quote_literal(s), quote_literal(n), quote_literal(stype)))
        if rows:
            return "".join(_to_str(r[0]) for r in rows)
    except Exception:
        pass
    if k == "trigger":
        try:
            rows = _q(conn, "SELECT TRIGGER_BODY FROM ALL_TRIGGERS WHERE OWNER=%s AND TRIGGER_NAME=%s"
                            % (quote_literal(s), quote_literal(n)))
            if rows and _to_str(rows[0][0]):
                return _to_str(rows[0][0])
        except Exception:
            pass
    return ""


def _catalog_dm(conn, schema, limit=400):
    """补全字典：表名 / 列名 / 类型（带长度），类型文本比 Oracle 版更贴近 DDL 写法。
    注意 limit 的语义是「最多收多少张表」（与 driver_catalog 一致），
    不是行数：按行截断会让 195 张表的库只覆盖前面二三十张。"""
    rows = _q(conn, "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, "
                    "CHAR_LENGTH, CHAR_USED FROM ALL_TAB_COLUMNS WHERE OWNER=%s "
                    "ORDER BY TABLE_NAME, COLUMN_ID" % quote_literal(_dm_up(schema)))
    out, seen = [], set()
    for r in rows:
        t = _to_str(r[0])
        if t not in seen:
            if limit and len(seen) >= limit:
                continue
            seen.add(t)
        out.append((t, _to_str(r[1]), _dm_type_text(r[2], r[3], r[4], r[5], r[6], r[7])))
    return out


# ---- SQLite ----
def _objects_sqlite(conn, schema):
    out = _empty_objects()
    for name, t in _q(conn, "SELECT name, type FROM sqlite_master "
                            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1"):
        out["view" if _to_str(t) == "view" else "table"].append(_to_str(name))
    return out


def _source_sqlite(conn, schema, name, kind):
    try:
        rows = _q(conn, "SELECT sql FROM sqlite_master WHERE name = %s" % quote_literal(name))
        return _to_str(rows[0][0]) if rows and rows[0][0] else ""
    except Exception:
        return ""


def _catalog_sqlite(conn, schema, limit=400):
    rows = []
    for (t,) in _q(conn, "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                         "AND name NOT LIKE 'sqlite_%' ORDER BY 1"):
        try:
            for c in _q(conn, "PRAGMA table_info(%s)" % quote_literal(_to_str(t))):
                rows.append((_to_str(t), _to_str(c[1]), _to_str(c[2])))
        except Exception:
            continue
    return rows


_OBJECTS_IMPL = {"postgresql": _objects_pg, "mysql": _objects_mysql, "sqlserver": _objects_mssql,
                 "oracle": _objects_ora, "dameng": _objects_dm, "sqlite": _objects_sqlite}
_SOURCE_IMPL = {"postgresql": _source_pg, "mysql": _source_mysql, "sqlserver": _source_mssql,
                "oracle": _source_ora, "dameng": _source_dm, "sqlite": _source_sqlite}
_CATALOG_IMPL = {"postgresql": _catalog_pg, "mysql": _catalog_mysql, "sqlserver": _catalog_mssql,
                 "oracle": _catalog_ora, "dameng": _catalog_dm, "sqlite": _catalog_sqlite}


def driver_objects(self, conn, schema):
    """对象树：一次把表/视图/过程/函数/序列/触发器列出来。取不到的类型留空。"""
    fn = _OBJECTS_IMPL.get(self.name)
    if not fn:
        return _empty_objects()
    try:
        return fn(conn, schema)
    except Exception:
        return _empty_objects()


def driver_source(self, conn, schema, name, kind):
    """对象定义（DDL）。取不到返回空串，前端显示「该库不提供定义查询」。"""
    fn = _SOURCE_IMPL.get(self.name)
    if not fn:
        return ""
    try:
        return fn(conn, schema, name, kind) or ""
    except Exception:
        return ""


def driver_catalog(self, conn, schema, limit=400):
    """补全字典：表名 + 列名 + 类型，一次批量查完（不走 N+1）。"""
    fn = _CATALOG_IMPL.get(self.name)
    if not fn:
        return []
    try:
        rows = fn(conn, schema, limit) or []
    except Exception:
        return []
    tables, order = {}, []
    for r in rows:
        t = _to_str(r[0])
        if t not in tables:
            if len(tables) >= limit:
                continue
            tables[t] = []
            order.append(t)
        tables[t].append([_to_str(r[1]), _to_str(r[2]) if len(r) > 2 else ""])
    return [{"name": t, "columns": tables[t]} for t in order]


# ---- 过程 / 函数参数（右击「执行」弹窗用）----
def _args_all_arguments(conn, schema, name, kind):
    """Oracle 与达梦共用 ALL_ARGUMENTS。
    实测达梦：POSITION 从 0 起；函数的返回值会以 ARGUMENT_NAME='V_RET' / IN_OUT='OUT'
    混在参数里，必须剔除，否则调用时凭空多一个参数。"""
    rows = _q(conn, "SELECT ARGUMENT_NAME, IN_OUT, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, "
                    "DATA_SCALE, CHAR_LENGTH, CHAR_USED, DEFAULT_VALUE "
                    "FROM ALL_ARGUMENTS WHERE OWNER=%s AND OBJECT_NAME=%s AND PACKAGE_NAME IS NULL "
                    "ORDER BY SEQUENCE"
                    % (quote_literal(_to_str(schema).upper()), quote_literal(_to_str(name).upper())))
    out = []
    for r in rows:
        an = _to_str(r[0])
        if not an or an.upper() in ("V_RET", "RETURN", "RETURN_VALUE"):
            continue
        out.append({"name": an, "mode": (_to_str(r[1]).upper() or "IN"),
                    "type": _dm_type_text(r[2], r[3], r[4], r[5], r[6], r[7]),
                    "default": _to_str(r[8])})
    return out


def _pg_array(v):
    """psycopg2 直接给 list；pg8000 可能给 {a,b} 文本，两种都接住。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [_to_str(x) for x in v]
    s = _to_str(v).strip()
    if not s or s == "{}":
        return []
    if s.startswith("{") and s.endswith("}"):
        body, out, buf, inq = s[1:-1], [], "", False
        for ch in body:
            if ch == '"':
                inq = not inq
                continue
            if ch == "," and not inq:
                out.append(buf)
                buf = ""
                continue
            buf += ch
        out.append(buf)
        return out
    return [s]


def _args_pg(conn, schema, name, kind):
    rows = _q(conn, "SELECT p.proargnames, p.proargmodes, "
                    "ARRAY(SELECT format_type(t, NULL) FROM unnest("
                    "COALESCE(p.proallargtypes, p.proargtypes)) AS t) "
                    "FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace "
                    "WHERE ns.nspname = %s AND p.proname = %s ORDER BY p.oid LIMIT 1"
                    % (quote_literal(_to_str(schema)), quote_literal(_to_str(name))))
    if not rows:
        return []
    names = _pg_array(rows[0][0])
    modes = _pg_array(rows[0][1])
    types = _pg_array(rows[0][2])
    out = []
    for i, t in enumerate(types):
        nm = names[i] if i < len(names) and names[i] else "arg%d" % (i + 1)
        md = "IN"
        if i < len(modes):
            md = {"i": "IN", "o": "OUT", "b": "INOUT", "v": "IN"}.get(modes[i], "IN")
        out.append({"name": nm, "mode": md, "type": t, "default": ""})
    return out


def _args_mysql(conn, schema, name, kind):
    rows = _q(conn, "SELECT PARAMETER_NAME, PARAMETER_MODE, DATA_TYPE, DTD_IDENTIFIER "
                    "FROM information_schema.PARAMETERS WHERE SPECIFIC_SCHEMA=%s AND SPECIFIC_NAME=%s "
                    "ORDER BY ORDINAL_POSITION"
                    % (quote_literal(schema), quote_literal(name)))
    return [{"name": _to_str(r[0]), "mode": (_to_str(r[1]) or "IN").upper(),
             "type": _to_str(r[3]) or _to_str(r[2]), "default": ""} for r in rows]


def _args_mssql(conn, schema, name, kind):
    rows = _q(conn, "SELECT p.name, TYPE_NAME(p.user_type_id) AS tn, p.is_output, p.max_length, "
                    "p.precision, p.scale FROM sys.parameters p "
                    "WHERE p.object_id = OBJECT_ID(%s) AND p.parameter_id > 0 "
                    "ORDER BY p.parameter_id"
                    % quote_literal("%s.%s" % (schema, name)))
    out = []
    for nm, tn, isout, maxlen, prec, scale in rows:
        t = _to_str(tn)
        low = t.lower()
        try:
            ml = int(maxlen)
        except Exception:
            ml = 0
        if low in ("varchar", "nvarchar", "char", "nchar", "varbinary", "binary") and ml:
            t += "(max)" if ml < 0 else "(%d)" % ml
        elif low in ("decimal", "numeric") and prec is not None:
            t += "(%s,%s)" % (prec, scale or 0)
        out.append({"name": _to_str(nm).lstrip("@"), "mode": "OUT" if isout else "IN",
                    "type": t, "default": ""})
    return out


def _args_none(conn, schema, name, kind):
    return []


_ARGS_IMPL = {"oracle": _args_all_arguments, "dameng": _args_all_arguments,
              "postgresql": _args_pg, "mysql": _args_mysql,
              "sqlserver": _args_mssql, "sqlite": _args_none}


def driver_proc_args(self, conn, schema, name, kind):
    """过程 / 函数的入参清单：[{name, mode, type, default}]。取不到就返回空表。"""
    fn = _ARGS_IMPL.get(self.name)
    if not fn:
        return []
    try:
        return fn(conn, schema, name, kind) or []
    except Exception:
        return []


Driver.objects = driver_objects
Driver.source = driver_source
Driver.catalog = driver_catalog
Driver.proc_args = driver_proc_args
