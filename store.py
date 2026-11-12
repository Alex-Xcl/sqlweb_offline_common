# -*- coding: utf-8 -*-
"""
状态存储 —— 单文件 SQLite（sqlweb.db）

为什么换：
    config.json / auth.json 是「读全量、改一处、写全量」的模型。多人同时改数据源、
    改账号时只能靠进程内锁串行化，而且一旦写入过程中进程被杀，靠 .tmp 换名只能兜住
    一半；账号、数据源、Redis 实例还散在两个文件里，备份和回滚都要拷好几份。
    换成单文件 SQLite 后：一次改动就是一个事务，被杀不会留半截；备份只拷 sqlweb.db；
    也能直接用 sqlite3 打开查。

表结构（都是"一行一条"，方便扩容与外部查询）：
    kv          标量配置项，如 title / host / port / readonly / max_rows，值按 JSON 存
    datasources 数据源，每行一个；key 主键，seq 保序，payload 存其余字段（含口令）
    redis       Redis 实例，同上
    users       账号，每行一个；口令仍是 PBKDF2-HMAC-SHA256 的 salt+hash，不落明文
    meta        版本号与迁移记录

迁移：
    首次启动若库里的数据源/账号都还是空的，而同目录存在旧的 config.json / auth.json，
    就自动导入，并把旧文件改名成 <原名>.migrated-<时间戳> 留档。只读旧文件，不删数据。

对外：
    SqliteStore(path)
        .load_config() / .save_config(cfg)
        .load_auth()   / .save_auth(data)
        .migrate_from_json(config_path, auth_path) -> dict 迁移摘要
"""
import json
import os
import sqlite3
import threading
import time

STATE_FILE = "sqlweb.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasources (
    key     TEXT PRIMARY KEY,
    seq     INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS redis (
    key     TEXT PRIMARY KEY,
    seq     INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user    TEXT PRIMARY KEY,
    seq     INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_datasources_seq ON datasources(seq);
CREATE INDEX IF NOT EXISTS ix_redis_seq ON redis(seq);
CREATE INDEX IF NOT EXISTS ix_users_seq ON users(seq);
"""


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False)


def _loads(text, default=None):
    try:
        return json.loads(text)
    except Exception:
        return default


class SqliteStore(object):
    """一个文件装全部运行状态。每次操作开一条短连接，靠事务保证原子性。"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------ 基础设施
    def _conn(self):
        if not os.path.isdir(os.path.dirname(os.path.abspath(self.path))):
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("PRAGMA journal_mode=DELETE")   # 不产生 -wal/-shm，备份就是拷一个文件
        c.execute("PRAGMA synchronous=FULL")
        return c

    def _init_schema(self):
        with self._lock:
            c = self._conn()
            try:
                c.executescript(_SCHEMA)
                c.commit()
            finally:
                c.close()
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def _count(self, table):
        c = self._conn()
        try:
            return int(c.execute("SELECT COUNT(1) FROM %s" % table).fetchone()[0])
        finally:
            c.close()

    def has_state(self):
        """库里是否已经有数据源或账号（判断要不要迁移）。"""
        return self._count("datasources") > 0 or self._count("users") > 0

    # ------------------------------------------------------------ 配置
    def load_config(self):
        """读回一份和原来 config.json 同形的 dict。"""
        c = self._conn()
        try:
            out = {}
            for k, v in c.execute("SELECT k, v FROM kv ORDER BY k"):
                out[k] = _loads(v, v)
            out["databases"] = [_loads(p, {}) for (p,) in
                                c.execute("SELECT payload FROM datasources ORDER BY seq")]
            out["redis"] = [_loads(p, {}) for (p,) in
                            c.execute("SELECT payload FROM redis ORDER BY seq")]
            return out
        finally:
            c.close()

    def save_config(self, cfg):
        """整体写入。一个事务：要么全落，要么全不落。"""
        scalars, dbs, insts = {}, [], []
        for k, v in (cfg or {}).items():
            if k.startswith("_"):
                continue
            if k == "databases":
                for i, d in enumerate(v or []):
                    if not isinstance(d, dict):
                        continue
                    row = {kk: vv for kk, vv in d.items() if not kk.startswith("_")}
                    dbs.append((str(row.get("key") or "ds%d" % (i + 1)), i, _dump(row)))
            elif k == "redis":
                for i, x in enumerate(v if isinstance(v, list) else ([v] if v else [])):
                    if not isinstance(x, dict):
                        continue
                    insts.append((str(x.get("key") or "redis%d" % (i + 1)), i, _dump(x)))
            else:
                scalars[k] = _dump(v)

        with self._lock:
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM kv")
                c.executemany("INSERT INTO kv(k, v) VALUES(?, ?)", sorted(scalars.items()))
                c.execute("DELETE FROM datasources")
                c.executemany("INSERT INTO datasources(key, seq, payload) VALUES(?, ?, ?)", dbs)
                c.execute("DELETE FROM redis")
                c.executemany("INSERT INTO redis(key, seq, payload) VALUES(?, ?, ?)", insts)
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()
        return self.path

    # ------------------------------------------------------------ 账号
    def load_auth(self):
        c = self._conn()
        try:
            users = [_loads(p, {}) for (p,) in
                     c.execute("SELECT payload FROM users ORDER BY seq")]
            return {"version": 2, "users": [u for u in users if isinstance(u, dict)]}
        finally:
            c.close()

    def save_auth(self, data):
        users = []
        for i, u in enumerate((data or {}).get("users") or []):
            if not isinstance(u, dict):
                continue
            users.append((str(u.get("user") or "u%d" % (i + 1)), i, _dump(u)))
        with self._lock:
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM users")
                c.executemany("INSERT INTO users(user, seq, payload) VALUES(?, ?, ?)", users)
                c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('auth_version', ?)",
                          (str((data or {}).get("version") or 2),))
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()
        return self.path

    # ------------------------------------------------------------ 迁移
    def _archive(self, path):
        """把已经导入完的旧文件改名留档，不删除。"""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = "%s.migrated-%s" % (path, stamp)
        n = 1
        while os.path.exists(target):
            target = "%s.migrated-%s_%d" % (path, stamp, n)
            n += 1
        os.replace(path, target)
        return target

    def migrate_from_json(self, config_path, auth_path):
        """首次启动：把旧 config.json / auth.json 导入。返回迁移摘要，供启动日志打印。"""
        summary = {"config": 0, "auth": 0, "archived": []}

        if self._count("datasources") == 0 and self._count("kv") == 0:
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                except Exception as e:
                    summary["config_error"] = "%s: %s" % (type(e).__name__, e)
                else:
                    if isinstance(raw, dict):
                        self.save_config(raw)
                        summary["config"] = len(raw.get("databases") or [])
                        summary["redis"] = len(raw.get("redis") or [])
                        try:
                            summary["archived"].append(self._archive(config_path))
                        except Exception:
                            pass

        if self._count("users") == 0:
            if os.path.exists(auth_path):
                try:
                    with open(auth_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                except Exception as e:
                    summary["auth_error"] = "%s: %s" % (type(e).__name__, e)
                else:
                    if isinstance(raw, dict):
                        # v1 单用户格式：顶层就是 user/salt/hash，包一层再写
                        if not isinstance(raw.get("users"), list) and raw.get("user"):
                            raw = {"version": 2, "users": [raw]}
                        self.save_auth(raw)
                        summary["auth"] = len(raw.get("users") or [])
                        try:
                            summary["archived"].append(self._archive(auth_path))
                        except Exception:
                            pass
        return summary
