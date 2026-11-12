# -*- coding: utf-8 -*-
"""进程内端到端自测（不随包交付，仅开发验证用）"""
import json, os, sqlite3, sys, tempfile, threading, time
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sqlweb

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
check("状态库权限 600（POSIX 生效，Windows 跳过）",
      os.name == "nt" or oct(os.stat(_sdb).st_mode)[-3:] == "600", oct(os.stat(_sdb).st_mode))

print()
print("=" * 52)
print("通过 %d 项，失败 %d 项" % (PASS_N[0], FAIL_N[0]))
print("=" * 52)
srv.shutdown()
sys.exit(0 if FAIL_N[0] == 0 else 1)
