# -*- coding: utf-8 -*-
"""进程内端到端自测（不随包交付，仅开发验证用）"""
import json, os, sqlite3, subprocess, sys, tempfile, threading, time


def _sp_run(args):
    """跑子进程并吞掉输出，只回退出码。"""
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sqlweb
import drivers

PORT = 18899
# 用系统临时目录，Windows 上没有 /tmp
TMPD = tempfile.gettempdir()
DB = os.path.join(TMPD, "sw_selftest.db")
CFG = os.path.join(TMPD, "sw_selftest_cfg.json")
AUTHP = os.path.join(TMPD, "sw_selftest_auth.json")

for p in (AUTHP, CFG):
    if os.path.exists(p):
        os.remove(p)

c = sqlite3.connect(DB)
c.execute("DROP TABLE IF EXISTS t_user")
c.execute("CREATE TABLE t_user(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
c.execute("DROP VIEW IF EXISTS v_user")
c.execute("CREATE VIEW v_user AS SELECT name, age FROM t_user WHERE age > 18")
c.executemany("INSERT INTO t_user(name, age) VALUES(?,?)",
              [("张三", 30), ("李四", 25), ("王五", 41)])
c.commit()
c.close()

with open(CFG, "w", encoding="utf-8") as f:
    json.dump({
        "title": "SQLWeb 测试",
        "host": "127.0.0.1", "port": PORT, "readonly": True, "max_rows": 100,
        "default_db": "lite",
        "databases": [{"key": "lite", "label": "本地 SQLite", "driver": "sqlite",
                       "path": DB, "readonly": True}],
        "redis": [],
    }, f, ensure_ascii=False)

cfg = sqlweb.normalize_redis(sqlweb.load_config(CFG))
sqlweb.AppState.cfg = cfg
sqlweb.AUTH_PATH = AUTHP
sqlweb.AppState.auth = sqlweb.AuthStore(AUTHP)
sqlweb.AppState.sessions = sqlweb.SessionManager()
sqlweb.AppState.limiter = sqlweb.RateLimiter()
sqlweb.AppState.captchas = sqlweb.CaptchaStore()
sqlweb.AppState.init_token = "654321"

from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", PORT), sqlweb.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

BASE = "http://127.0.0.1:%d" % PORT
COOKIE = {"v": ""}
PASS_N = [0]
FAIL_N = [0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def call(method, path, body=None, cookie=None, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = OPENER.open(req, timeout=15)
        sc = resp.getcode()
        hdrs = resp.headers
        payload = resp.read()
    except urllib.error.HTTPError as e:
        sc = e.code
        hdrs = e.headers
        payload = e.read()
    try:
        body_obj = json.loads(payload.decode("utf-8"))
    except Exception:
        body_obj = payload[:200]
    return sc, hdrs, body_obj


def check(name, cond, extra=""):
    if cond:
        PASS_N[0] += 1
        print("  [PASS] %s" % name)
    else:
        FAIL_N[0] += 1
        print("  [FAIL] %s  %s" % (name, extra))


print("=== 1. 未登录访问 ===")
sc, hd, b = call("GET", "/")
check("GET / 未登录跳转 302", sc == 302, "got %s" % sc)
sc, hd, b = call("GET", "/api/session")
check("session 未初始化", sc == 200 and b.get("initialized") is False, str(b)[:120])
sc, hd, b = call("GET", "/api/config")
check("未登录访问 /api/config 返回 401", sc == 401, "got %s" % sc)

print("=== 2. 初始化管理员 ===")
sc, hd, b = call("POST", "/api/setup", {"username": "admin", "password": "GoodPass123", "token": "000000"})
check("错误令牌被拒", sc == 400, str(b)[:120])
sc, hd, b = call("POST", "/api/setup", {"username": "admin", "password": "123", "token": "654321"})
check("口令过短被拒", sc == 400, str(b)[:120])
sc, hd, b = call("POST", "/api/setup", {"username": "admin", "password": "GoodPass123", "token": "654321"})
check("正确令牌初始化成功", sc == 200 and b.get("ok") is True, str(b)[:120])
check("auth.json 已生成", os.path.exists(AUTHP))
sc, hd, b = call("POST", "/api/setup", {"username": "admin", "password": "Other12345", "token": "654321"})
check("重复初始化被拒", sc == 400, str(b)[:120])

print("=== 3. 登录 + 验证码 ===")
sc, hd, b = call("POST", "/api/login", {"username": "admin", "password": "GoodPass123",
                                        "captcha_id": "bogus", "captcha": "0000"})
check("验证码失效被拒", sc == 400, str(b)[:120])

cid = sqlweb.AppState.captchas.put("1234")
sc, hd, b = call("POST", "/api/login", {"username": "admin", "password": "GoodPass123",
                                        "captcha_id": cid, "captcha": "9999"})
check("验证码错误被拒", sc == 400 and "验证码" in str(b), str(b)[:120])

cid = sqlweb.AppState.captchas.put("1234")
sc, hd, b = call("POST", "/api/login", {"username": "admin", "password": "WrongPass123",
                                        "captcha_id": cid, "captcha": "1234"})
check("口令错误被拒", sc == 401, str(b)[:120])

cid = sqlweb.AppState.captchas.put("1234")
sc, hd, b = call("POST", "/api/login", {"username": "nobody", "password": "GoodPass123",
                                        "captcha_id": cid, "captcha": "1234"})
check("账号错误被拒（与口令错误同一提示）", sc == 401 and "用户名或口令" in str(b), str(b)[:120])

cid = sqlweb.AppState.captchas.put("1234")
sc, hd, b = call("POST", "/api/login", {"username": "admin", "password": "GoodPass123",
                                        "captcha_id": cid, "captcha": "1234"})
setck = hd.get("Set-Cookie") or ""
check("登录成功", sc == 200 and b.get("ok") is True, str(b)[:120])
check("下发 httpOnly Cookie", "sw_session=" in setck and "HttpOnly" in setck, setck[:160])
check("Cookie 含 SameSite=Strict", "SameSite=Strict" in setck, setck[:160])
COOKIE["v"] = setck.split(";")[0]

print("=== 4. 已登录后 API ===")
sc, hd, b = call("GET", "/api/config", cookie=COOKIE["v"])
check("/api/config 可访问", sc == 200 and b.get("ok") is True, str(b)[:120])
check("数据源列表非空", len(b.get("databases") or []) == 1, str(b)[:200])

sc, hd, b = call("GET", "/api/drivers", cookie=COOKIE["v"])
check("/api/drivers 返回 6 种数据库", sc == 200 and len(b.get("data") or []) == 6, str(b)[:200])

sc, hd, b = call("GET", "/api/overview?db=lite", cookie=COOKIE["v"])
check("对象树 overview 成功", sc == 200 and b.get("ok") is True, str(b)[:200])
print("       schemas =", b.get("data", {}).get("schemas"))

sc, hd, b = call("GET", "/api/tables?db=lite&schema=main", cookie=COOKIE["v"])
check("表清单包含 t_user", sc == 200 and "t_user" in (b.get("data") or []), str(b)[:200])

sc, hd, b = call("GET", "/api/columns?db=lite&schema=main&table=t_user", cookie=COOKIE["v"])
check("列信息 3 列", sc == 200 and len(b.get("data") or []) == 3, str(b)[:300])
print("       columns =", b.get("data"))

print("=== 4b. 对象树（视图/过程/函数分组）===")
sc, hd, b = call("GET", "/api/objects?db=lite&schema=main", cookie=COOKIE["v"])
_obj = b.get("data") or {}
check("objects 接口返回分组结构", sc == 200 and b.get("ok") is True and "table" in _obj, str(b)[:200])
check("表分组含 t_user、视图分组含 v_user",
      "t_user" in (_obj.get("table") or []) and "v_user" in (_obj.get("view") or []),
      str(_obj)[:200])
check("SQLite 无存储过程/函数（分组存在但为空）",
      _obj.get("procedure") == [] and _obj.get("function") == [], str(_obj)[:200])

sc, hd, b = call("GET", "/api/source?db=lite&schema=main&name=v_user&kind=view", cookie=COOKIE["v"])
_src = b.get("data") or ""
check("视图定义可取回且是 CREATE VIEW", sc == 200 and "CREATE VIEW" in _src.upper(), str(b)[:200])

sc, hd, b = call("GET", "/api/source?db=lite&schema=main&name=no_such_obj&kind=view", cookie=COOKIE["v"])
check("对象不存在时返回空定义而不是报错", sc == 200 and (b.get("data") or "") == "", str(b)[:200])

sc, hd, b = call("GET", "/api/catalog?db=lite&schema=main", cookie=COOKIE["v"])
_cat = b.get("data") or []
_tn = [t for t in _cat if t.get("name") == "t_user"]
check("补全字典带出表及列", sc == 200 and _tn and len(_tn[0].get("columns") or []) == 3,
      str(_cat)[:300])

sc, hd, b = call("GET", "/api/objects?db=lite&schema=main", cookie=COOKIE["v"])
check("对象树二次请求命中缓存（仍返回同样数据）",
      sc == 200 and "t_user" in ((b.get("data") or {}).get("table") or []), str(b)[:200])

print("=== 5. SQL 执行 ===")
sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "SELECT * FROM t_user ORDER BY id"}, cookie=COOKIE["v"])
check("SELECT 成功返回 3 行", sc == 200 and b.get("ok") and len(b.get("rows") or []) == 3, str(b)[:300])
print("       rows =", b.get("rows"))

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "SELECT name, age FROM t_user WHERE age > 28"}, cookie=COOKIE["v"])
check("带 WHERE 查询成功", sc == 200 and len(b.get("rows") or []) == 2, str(b)[:300])

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "INSERT INTO t_user(name, age) VALUES('赵六', 50)"}, cookie=COOKIE["v"])
check("只读模式拦截 INSERT", sc == 200 and b.get("ok") is False and "只读" in str(b), str(b)[:200])

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "DROP TABLE t_user"}, cookie=COOKIE["v"])
check("只读模式拦截 DROP", sc == 200 and b.get("ok") is False, str(b)[:200])

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "-- 注释\nSELECT count(*) AS cnt FROM t_user"}, cookie=COOKIE["v"])
check("行注释不干扰识别", sc == 200 and b.get("ok") is True and b.get("rows") == [[3]], str(b)[:200])

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "SELECT * FROM t_user ORDER BY id LIMIT 2;"}, cookie=COOKIE["v"])
check("分号结尾正常执行", sc == 200 and len(b.get("rows") or []) == 2, str(b)[:200])

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "SELECT * FROM no_such_table"}, cookie=COOKIE["v"])
check("执行期错误返回驱动原文",
      sc == 200 and b.get("ok") is False and "no_such_table" in str(b), str(b)[:200])
print("       驱动原文 =", str(b.get("error", {}).get("message"))[:120])

sc, hd, b = call("POST", "/api/query", {"db": "lite", "sql": "SELECT 1/0 AS zero"}, cookie=COOKIE["v"])
check("表达式计算结果为 NULL 属 SQLite 正常行为",
      sc == 200 and b.get("ok") is True and b.get("rows") == [[None]], str(b)[:150])

print("=== 6. 数据源管理 ===")
sc, hd, b = call("POST", "/api/ds/test", {"ds": {"driver": "sqlite", "path": DB, "label": "t"}}, cookie=COOKIE["v"])
check("SQLite 连接测试成功", sc == 200 and b.get("ok") is True, str(b)[:250])
print("       server =", b.get("server"))

sc, hd, b = call("POST", "/api/ds/test", {"ds": {"driver": "sqlite", "path": "/nope/x.db", "label": "t"}}, cookie=COOKIE["v"])
check("错误路径返回真实报错", sc == 200 and b.get("ok") is False and "不存在" in str(b), str(b)[:250])
print("       真实报错 =", str(b.get("error", {}).get("message"))[:120])

# 用不存在的驱动名验证"驱动缺失/不支持"提示。
# 原来用 sqlserver，靠的是"pymssql 不在 wheels 里"这个环境假设；
# 换成 --system-site-packages 的 venv 后系统已装 pymssql，这条就变成了真去连 1433，
# 断言随之失效。改用未知驱动名，不依赖任何环境。
sc, hd, b = call("POST", "/api/ds/test", {"ds": {"driver": "nosuchdb", "host": "127.0.0.1",
                                                "port": 1433, "user": "sa", "password": "x", "label": "t"}},
                 cookie=COOKIE["v"])
check("不支持的类型给出可选清单",
      sc == 400 and "不支持的数据库类型" in str(b) and "postgresql" in str(b), str(b)[:250])
print("       提示 =", str(b.get("error", {}).get("message"))[:150])

# 驱动已装但连不上时，同样要给明确报错（不能静默）
sc, hd, b = call("POST", "/api/ds/test", {"ds": {"driver": "mysql", "host": "127.0.0.1",
                                                "port": 3306, "user": "root", "password": "x", "label": "t"}},
                 cookie=COOKIE["v"])
msg = str(b.get("error", {}).get("message", ""))
check("连接失败时返回驱动原始报错而非空响应",
      b.get("ok") is False and len(msg) > 0 and ("驱动不可用" in msg or "OperationalError" in msg
                                                 or "connect" in msg.lower() or "拒绝" in msg),
      msg[:180])
print("       提示 =", msg[:150])

sc, hd, b = call("POST", "/api/ds/save", {"ds": {"key": "lite2", "label": "第二个 SQLite",
                                                "driver": "sqlite", "path": DB, "readonly": True}},
                 cookie=COOKIE["v"])
check("新增数据源成功", sc == 200 and b.get("ok") is True, str(b)[:200])
sc, hd, b = call("GET", "/api/ds", cookie=COOKIE["v"])
check("数据源数量变为 2", len(b.get("data") or []) == 2, str(b)[:250])
c2 = json.load(open(CFG, encoding="utf-8"))
check("config.json 落盘 2 个数据源", len(c2.get("databases") or []) == 2, str(c2)[:200])

sc, hd, b = call("POST", "/api/ds/default", {"key": "lite2"}, cookie=COOKIE["v"])
check("设默认成功", sc == 200 and b.get("ok") is True, str(b)[:150])

sc, hd, b = call("POST", "/api/ds/delete", {"key": "lite2"}, cookie=COOKIE["v"])
check("删除数据源成功", sc == 200 and b.get("ok") is True, str(b)[:200])

print("=== 7. 改密与登出 ===")
sc, hd, b = call("POST", "/api/password", {"old": "bad", "new": "NewPass1234"}, cookie=COOKIE["v"])
check("原口令错误被拒", sc == 400, str(b)[:150])

sc, hd, b = call("GET", "/api/config", cookie=COOKIE["v"])
check("改密失败后会话仍有效", sc == 200, "got %s" % sc)

sc, hd, b = call("POST", "/api/password", {"old": "GoodPass123", "new": "NewPass1234"}, cookie=COOKIE["v"])
check("改密成功", sc == 200 and b.get("ok") is True, str(b)[:150])

sc, hd, b = call("GET", "/api/config", cookie=COOKIE["v"])
check("改密后旧会话失效", sc == 401, "got %s" % sc)

cid = sqlweb.AppState.captchas.put("4321")
sc, hd, b = call("POST", "/api/login", {"username": "admin", "password": "NewPass1234",
                                        "captcha_id": cid, "captcha": "4321"})
check("新口令可登录", sc == 200 and b.get("ok") is True, str(b)[:150])
COOKIE["v"] = (hd.get("Set-Cookie") or "").split(";")[0]

sc, hd, b = call("POST", "/api/logout", {}, cookie=COOKIE["v"])
check("登出成功", sc == 200, str(b)[:120])
sc, hd, b = call("GET", "/api/config", cookie=COOKIE["v"])
check("登出后会话失效", sc == 401, "got %s" % sc)

print("=== 8. 限速 ===")
sqlweb.AppState.limiter = sqlweb.RateLimiter()
blocked_at = None
for i in range(8):
    cid = sqlweb.AppState.captchas.put("1234")
    sc, hd, b = call("POST", "/api/login", {"username": "admin", "password": "bad",
                                            "captcha_id": cid, "captcha": "1234"})
    if sc == 429:
        blocked_at = i
        break
check("连续失败触发 429 限速", blocked_at is not None, "blocked_at=%s" % blocked_at)

print("=== 9. 验证码图片 ===")
sc, hd, b = call("GET", "/api/captcha")
check("验证码接口返回 PNG data-uri", sc == 200 and str(b.get("image", "")).startswith("data:image/png;base64,"), str(b)[:120])
import base64 as _b64
img = _b64.b64decode(str(b.get("image", "").split(",", 1)[1]))
check("PNG 头正确", img[:8] == b"\x89PNG\r\n\x1a\n", repr(img[:8]))
check("图片体积极小(<3KB)", len(img) < 3000, "%d bytes" % len(img))

print("=== 10. 状态库（SQLite 单文件）===")
import store as _store
_sdb = os.path.join(TMPD, "sw_selftest_store.db")
for _p in (_sdb, _sdb + "-journal"):
    if os.path.exists(_p):
        os.remove(_p)
_st = _store.SqliteStore(_sdb)
_tb = [r[0] for r in sqlite3.connect(_sdb).execute("SELECT name FROM sqlite_master WHERE type='table'")]
check("SQLite 表结构齐全", {"kv", "datasources", "redis", "users", "meta"} <= set(_tb), str(_tb))
_ocfg = os.path.join(TMPD, "sw_selftest_legacy_cfg.json")
_oauth = os.path.join(TMPD, "sw_selftest_legacy_auth.json")
with open(_ocfg, "w", encoding="utf-8") as f:
    json.dump({"title": "迁移用", "port": 8899,
               "databases": [{"key": "x", "label": "X", "driver": "sqlite", "path": "/x.db"}],
               "redis": [{"key": "r", "label": "R", "host": "127.0.0.1", "port": 6379, "db": 0}]},
              f, ensure_ascii=False)
with open(_oauth, "w", encoding="utf-8") as f:
    json.dump({"version": 2, "users": [{"user": "admin", "salt": "s", "hash": "h", "role": "admin"}]},
              f, ensure_ascii=False)
_mig = _st.migrate_from_json(_ocfg, _oauth)
check("旧 config.json 迁入数据源", _mig.get("config") == 1, str(_mig)[:160])
check("旧 auth.json 迁入账号", _mig.get("auth") == 1, str(_mig)[:160])
check("迁移后旧文件改名留档（不删数据）",
      not os.path.exists(_ocfg) and len(_mig.get("archived") or []) == 2, str(_mig.get("archived")))
_back = _st.load_config()
check("状态库回读数据源与 Redis 实例",
      (_back.get("databases") or [{}])[0].get("key") == "x" and
      (_back.get("redis") or [{}])[0].get("key") == "r", str(_back)[:180])
check("状态库回读账号", [u["user"] for u in _st.load_auth()["users"]] == ["admin"], "")
_mig2 = _st.migrate_from_json(_ocfg, _oauth)
check("重复迁移不会重复导入", _mig2.get("config") == 0 and _mig2.get("auth") == 0, str(_mig2))
# setconfig.py：离线改配置必须真的落库（install.sh --port / --base-path 走的就是它）
_ret = _sp_run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "setconfig.py"),
                "--db", _sdb, "--set", "base_path=/sqlweb", "--set", "port=18099"])
check("setconfig.py 能写状态库（退出码 0）", _ret == 0, "rc=%s" % _ret)
_sc = _store.SqliteStore(_sdb).load_config()
check("setconfig.py 写入 base_path / port 正确",
      (_sc.get("base_path") == "/sqlweb") and (int(_sc.get("port") or 0) == 18099), str(_sc)[:120])
_ret2 = _sp_run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "setconfig.py"),
                 "--db", _sdb, "--set", "base_path=/"])
_sc2 = _store.SqliteStore(_sdb).load_config()
check("setconfig.py 把 base_path=/ 规范成空（根路径）", _ret2 == 0 and _sc2.get("base_path") == "", repr(_sc2.get("base_path")))

# 语句切分：达梦 / Oracle 的程序块不能被分号拆开（否则 CREATE PROCEDURE 必报语法错）
_p_proc = """CREATE OR REPLACE PROCEDURE EALEPM.PROC_X(p_year VARCHAR(2000))
AS
BEGIN
  NULL;
END;"""
_s1 = sqlweb.split_statements(_p_proc)
check("存储过程整段提交（不被分号拆成两条）", len(_s1) == 1 and _s1[0].rstrip().endswith("END"), _s1)
_s2 = sqlweb.split_statements("BEGIN; INSERT INTO t VALUES(1); COMMIT;")
check("事务 BEGIN; 仍按分号切三条", len(_s2) == 3, _s2)
_s3 = sqlweb.split_statements("SELECT CASE WHEN 1=1 THEN 1 END; SELECT 2;")
check("块外 CASE 表达式不吞下一条", len(_s3) == 2, _s3)
_blk = """CREATE OR REPLACE PROCEDURE P AS
BEGIN
  IF 1=1 THEN NULL; END IF;
  CASE WHEN 1=1 THEN NULL; END CASE;
  NULL;
END;
SELECT 1;"""
_s4 = sqlweb.split_statements(_blk)
check("块内 END IF / END CASE 平衡，后续语句正常",
      len(_s4) == 2 and _s4[1].strip() == "SELECT 1", _s4)
_s5 = sqlweb.split_statements("BEGIN NULL; END;\n/\n")
check("匿名块整段、SQL*Plus 的 / 丢掉", len(_s5) == 1, _s5)
_ro_ok, _ro_msg = sqlweb.readonly_check("CREATE OR REPLACE PROCEDURE P AS BEGIN NULL; END;")
check("只读拦截 DDL 的提示指出去哪改",
      (not _ro_ok) and ("数据源管理" in _ro_msg), _ro_msg)

# 整段提交：块式 DDL 只喂给驱动一次（旧实现会切成 "……BEGIN NULL" + "END" 两条）
class _FakeCur(object):
    def __init__(self, rec):
        self.rec, self.description = rec, None

    def execute(self, st):
        self.rec.append(st)

    def fetchall(self):
        return []

    def close(self):
        pass


class _FakeConn(object):
    def __init__(self, rec):
        self.rec = rec

    def cursor(self):
        return _FakeCur(self.rec)

    def commit(self):
        pass

    def close(self):
        pass


class _FakeDriver(object):
    name = "fake"

    def __init__(self, rec):
        self.rec = rec

    def connect(self, ds, timeout=10, statement_timeout_ms=30000):
        return _FakeConn(self.rec)


_rec = []
_orig_get = drivers.get_driver
drivers.get_driver = lambda n: _FakeDriver(_rec)
try:
    sqlweb.run_query({"readonly": False, "connect_timeout": 10, "statement_timeout_ms": 30000},
                     {"driver": "fake", "readonly": False}, _p_proc, 100)
finally:
    drivers.get_driver = _orig_get
check("块式 DDL 整段只提交一次（不拆成两条碎片）",
      len(_rec) == 1 and _rec[0].strip().upper().startswith("CREATE")
      and _rec[0].rstrip().endswith("END"), _rec)

check("状态库权限 600（POSIX 生效，Windows 跳过）",
      os.name == "nt" or oct(os.stat(_sdb).st_mode)[-3:] == "600", oct(os.stat(_sdb).st_mode))

# 元数据缓存：命中不重复查库；refresh 强制重查
_hits = [0]
_dummy = {"key": "lite", "database": ""}


def _fetch():
    _hits[0] += 1
    return {"n": _hits[0]}


sqlweb.meta_cache_clear()
_a = sqlweb.meta_cached("t", sqlweb.meta_key(_dummy, "main"), False, _fetch)
_b = sqlweb.meta_cached("t", sqlweb.meta_key(_dummy, "main"), False, _fetch)
_c = sqlweb.meta_cached("t", sqlweb.meta_key(_dummy, "main"), True, _fetch)
check("元数据缓存命中后不重复查询", _a == _b == {"n": 1}, "hits=%d" % _hits[0])
check("refresh=1 强制重查（拿到新值）", _c == {"n": 2}, "hits=%d" % _hits[0])
sqlweb.meta_cache_clear()


# 连接复用：验活通过就复用同一条；验活失败就丢弃不复用
class _ProbeCur(object):
    def __init__(self, ok):
        self.ok = ok

    def execute(self, sql):
        if not self.ok:
            raise RuntimeError("conn dead")

    def fetchall(self):
        return []

    def close(self):
        pass


class _ProbeConn(object):
    def __init__(self, ok):
        self.ok = ok
        self.closed = False

    def cursor(self):
        return _ProbeCur(self.ok)

    def close(self):
        self.closed = True


_pc = _ProbeConn(True)
sqlweb.CONN_POOL.put("k1", _pc)
check("连接复用：验活通过拿回同一条连接", sqlweb.CONN_POOL.get("k1", "SELECT 1") is _pc)
_dead = _ProbeConn(False)
sqlweb.CONN_POOL.put("k2", _dead)
check("连接复用：验活失败丢弃并关闭", sqlweb.CONN_POOL.get("k2", "SELECT 1") is None and _dead.closed)
check("连接复用：空闲上限 2 条，超出直接关掉",
      (lambda: (sqlweb.CONN_POOL.drop_all(),
                [sqlweb.CONN_POOL.put("k3", _ProbeConn(True)) for _ in range(3)],
                len(sqlweb.CONN_POOL._idle.get("k3", [])) == 2)[-1])())
sqlweb.CONN_POOL.drop_all()

# 子路径模式：带前缀通、不带前缀 404 且页面写明正确地址与 nginx 写法
_old_base = sqlweb.AppState.cfg.get("base_path")
sqlweb.AppState.cfg["base_path"] = "/sqlweb"
try:
    sc, hd, b = call("GET", "/sqlweb/login")
    check("base_path 模式：带前缀能打开登录页", sc == 200, "got %s" % sc)
    sc, hd, b = call("GET", "/login")
    try:
        _r = urllib.request.urlopen(BASE + "/login", timeout=10)
        _sc2, _txt = _r.getcode(), _r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as _e:
        _sc2, _txt = _e.code, _e.read().decode("utf-8", "replace")
    check("base_path 模式：不带前缀 404 且提示正确路径与 proxy_pass",
          sc == 404 and _sc2 == 404 and "访问前缀" in _txt and "结尾不要加斜杠" in _txt,
          "got %s %s" % (sc, _txt[:150]))
    sc, hd, b = call("GET", "/sqlweb/")
    check("base_path 模式：带前缀根地址可跳转登录", sc in (200, 302), "got %s" % sc)
finally:
    sqlweb.AppState.cfg["base_path"] = _old_base

print()
print("=" * 52)
print("通过 %d 项，失败 %d 项" % (PASS_N[0], FAIL_N[0]))
print("=" * 52)
srv.shutdown()
sys.exit(0 if FAIL_N[0] == 0 else 1)
