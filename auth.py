# -*- coding: utf-8 -*-
"""
认证与会话 —— sqlweb_offline_common

设计目标：
    1. 强制登录：没有 auth.json 时服务进入"初始化模式"，必须先设管理员口令
    2. 口令不落明文：PBKDF2-HMAC-SHA256 + 随机 salt（20 万次迭代）
    3. 多账号：每个账号独立口令哈希 + 独立授权范围（能看到哪些数据源）
    4. 会话用随机 token，放 httpOnly + SameSite=Strict 的 Cookie
    5. 登录失败按「IP+账号」限速，防暴力破解，同时不互相牵连
    6. 首次初始化需要一个控制台打印的一次性令牌，防止被别人抢先设密码

auth.json 结构（v2，多用户）：
    {
      "version": 2,
      "users": [
        {"user": "admin", "salt": "...", "hash": "...", "iterations": 200000,
         "role": "admin", "allow_dbs": null, "updated": "2026-11-12T09:46:32"},
        {"user": "zhangsan", "salt": "...", "hash": "...", "iterations": 200000,
         "role": "user", "allow_dbs": ["MUCONFIG"], "updated": "..."}
      ]
    }

    role:       admin = 可管数据源/管账号/看全部
                user  = 只能看 allow_dbs 里授权的数据源
    allow_dbs:  null = 全部（仅 admin 默认为此）
                []   = 一个都没有
                ["k1","k2"] = 只这几个

    兼容 v1 单用户格式（顶层直接是 user/salt/hash）：加载时自动迁移成
    users[0]（role=admin, allow_dbs=null），下次写盘即固化为 v2。

对外：
    AuthStore(path)
        .initialized / .user / .users() / .get(u) / .role_of(u)
        .verify(u,p) / .set_password(u,p) / .add_user(...) / .update_user(...)
        .remove_user(u) / .change_password(u, old, new)
    SessionManager()
        .create / get / destroy / destroy_user / destroy_all / sweep
    RateLimiter()     按 key 限速（key 由调用方拼成 ip|user）
    CaptchaStore()
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

PBKDF2_ITER = 200000
SESSION_TTL = 8 * 3600         # 会话最长 8 小时
SESSION_IDLE = 30 * 60         # 闲置 30 分钟失效
MAX_FAILS_PER_MIN = 5

ROLE_ADMIN = "admin"
ROLE_USER = "user"

# 哨兵：区分"不修改这个字段"与"把它改成 None"
_KEEP = object()


def _hex(b):
    return b.hex()


def _pbkdf2(password, salt_bytes, iterations=PBKDF2_ITER):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes,
                               iterations, 32)


def parse_cookie(header):
    """把 Cookie 头解析成 dict。"""
    out = {}
    if not header:
        return out
    for part in header.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class AuthStore(object):
    """账号口令存储。文件只保存 salt + hash，不保存明文。"""

    # 供调用方区分"不修改授权"与"清空授权"：update_user(allow_dbs=AuthStore.KEEP)
    KEEP = _KEEP

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = {"version": 2, "users": []}
        self._load()

    # ---------------------------------------------------------------- 加载
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            self._data = {"version": 2, "users": []}
            return

        if isinstance(raw.get("users"), list):
            users = []
            for u in raw["users"]:
                if not isinstance(u, dict):
                    continue
                if not (u.get("user") and u.get("salt") and u.get("hash")):
                    continue
                u = dict(u)
                u.setdefault("iterations", PBKDF2_ITER)
                u.setdefault("role", ROLE_ADMIN if len(users) == 0 else ROLE_USER)
                if u["role"] not in (ROLE_ADMIN, ROLE_USER):
                    u["role"] = ROLE_USER
                if u["role"] == ROLE_ADMIN:
                    u["allow_dbs"] = None
                    u["allow_redis"] = None
                else:
                    if not isinstance(u.get("allow_dbs"), list):
                        u["allow_dbs"] = []
                    if not isinstance(u.get("allow_redis"), list):
                        u["allow_redis"] = []
                users.append(u)
            self._data = {"version": 2, "users": users}
            return

        # ---- 兼容 v1 单用户格式 ----
        if raw.get("user") and raw.get("salt") and raw.get("hash"):
            self._data = {"version": 2, "users": [{
                "user": raw["user"],
                "salt": raw["salt"],
                "hash": raw["hash"],
                "iterations": int(raw.get("iterations") or PBKDF2_ITER),
                "role": ROLE_ADMIN,
                "allow_dbs": None,
                "allow_redis": None,
                "updated": raw.get("updated") or "",
            }]}
            return

        self._data = {"version": 2, "users": []}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- 查询
    @property
    def initialized(self):
        return bool(self._data.get("users"))

    @property
    def user(self):
        """兼容属性：返回管理员账号名（没有管理员则取第一个）。"""
        users = self._data.get("users") or []
        for u in users:
            if u.get("role") == ROLE_ADMIN:
                return u["user"]
        return users[0]["user"] if users else "admin"

    def get(self, user):
        for u in (self._data.get("users") or []):
            if u["user"] == (user or ""):
                return u
        return None

    def role_of(self, user):
        u = self.get(user)
        return (u or {}).get("role") or ROLE_USER

    def is_admin(self, user):
        return self.role_of(user) == ROLE_ADMIN

    def users(self):
        """给管理界面看的账号清单（不含 salt/hash）。"""
        out = []
        for u in (self._data.get("users") or []):
            out.append({
                "user": u["user"],
                "role": u.get("role") or ROLE_USER,
                "allow_dbs": None if u.get("allow_dbs") is None else list(u["allow_dbs"]),
                "allow_redis": None if u.get("allow_redis") is None else list(u["allow_redis"]),
                "updated": u.get("updated") or "",
            })
        return out

    # ---------------------------------------------------------------- 写
    @staticmethod
    def _make_entry(user, password, role=ROLE_USER, allow_dbs=None, allow_redis=None):
        if not user or not user.strip():
            raise ValueError("用户名不能为空")
        if not password or len(password) < 8:
            raise ValueError("口令至少 8 位")
        salt = secrets.token_bytes(16)
        dk = _pbkdf2(password, salt)
        if role == ROLE_ADMIN:
            allow_dbs = None
            allow_redis = None
        else:
            allow_dbs = [] if allow_dbs is None else [str(x) for x in allow_dbs]
            allow_redis = [] if allow_redis is None else [str(x) for x in allow_redis]
        return {
            "user": user.strip(),
            "salt": _hex(salt),
            "hash": _hex(dk),
            "iterations": PBKDF2_ITER,
            "role": role,
            "allow_dbs": allow_dbs,
            "allow_redis": allow_redis,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def set_password(self, user, password):
        """初始化首个管理员 / 重设某个既有账号的口令。"""
        with self._lock:
            users = self._data.setdefault("users", [])
            exist = None
            for u in users:
                if u["user"] == (user or "").strip():
                    exist = u
                    break
            if exist:
                fresh = self._make_entry(exist["user"], password,
                                        exist.get("role") or ROLE_USER,
                                        exist.get("allow_dbs"))
                for k in ("salt", "hash", "iterations", "updated"):
                    exist[k] = fresh[k]
            else:
                role = ROLE_ADMIN if not users else ROLE_USER
                users.append(self._make_entry(user, password, role, None))
            self._save()
            return True

    def add_user(self, user, password, role=ROLE_USER, allow_dbs=None, allow_redis=None):
        user = (user or "").strip()
        if not user:
            raise ValueError("用户名不能为空")
        with self._lock:
            if any(u["user"] == user for u in (self._data.get("users") or [])):
                raise ValueError("账号已存在：%s" % user)
            self._data.setdefault("users", []).append(
                self._make_entry(user, password, role, allow_dbs, allow_redis))
            self._save()
        return True

    def update_user(self, user, role=None, allow_dbs=_KEEP, allow_redis=_KEEP, password=None):
        """改角色 / 改数据源与 Redis 授权 / 可选改口令。role=None、allow_*=_KEEP 表示不修改。"""
        with self._lock:
            u = self.get(user)
            if not u:
                raise ValueError("账号不存在：%s" % user)
            if role in (ROLE_ADMIN, ROLE_USER):
                u["role"] = role
                if role == ROLE_ADMIN:
                    u["allow_dbs"] = None
                    u["allow_redis"] = None
                else:
                    if u.get("allow_dbs") is None:
                        u["allow_dbs"] = []
                    if u.get("allow_redis") is None:
                        u["allow_redis"] = []
            if allow_dbs is not _KEEP and u.get("role") != ROLE_ADMIN:
                u["allow_dbs"] = [] if allow_dbs is None else [str(x) for x in allow_dbs]
            if allow_redis is not _KEEP and u.get("role") != ROLE_ADMIN:
                u["allow_redis"] = [] if allow_redis is None else [str(x) for x in allow_redis]
            if password:
                if len(password) < 8:
                    raise ValueError("口令至少 8 位")
                salt = secrets.token_bytes(16)
                dk = _pbkdf2(password, salt)
                u["salt"], u["hash"] = _hex(salt), _hex(dk)
                u["iterations"] = PBKDF2_ITER
            u["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save()
        return True

    def remove_user(self, user):
        with self._lock:
            users = self._data.get("users") or []
            if not any(u["user"] == user for u in users):
                raise ValueError("账号不存在：%s" % user)
            rest = [u for u in users if u["user"] != user]
            if not any(u.get("role") == ROLE_ADMIN for u in rest):
                raise ValueError("不能删除最后一个管理员账号")
            self._data["users"] = rest
            self._save()
        return True

    def change_password(self, user, old, new):
        if not self.verify(user, old):
            raise ValueError("原口令不正确")
        return self.set_password(user, new)

    def verify(self, user, password):
        u = self.get(user)
        if not u:
            # 账号不存在也跑一次假校验，避免时序差异泄露账号是否存在
            prob = (self._data.get("users") or [{}])[0]
            try:
                _pbkdf2(password or "x", bytes.fromhex(prob.get("salt") or "00"))
            except Exception:
                pass
            return False
        salt = bytes.fromhex(u["salt"])
        expect = bytes.fromhex(u["hash"])
        got = _pbkdf2(password or "", salt, int(u.get("iterations") or PBKDF2_ITER))
        return hmac.compare_digest(got, expect)


class SessionManager(object):
    """进程内会话表。重启服务即全部失效，符合离线运维工具的预期。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def create(self, user, ip):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[token] = {
                "user": user, "ip": ip, "created": now, "last": now,
            }
        return token

    def get(self, token):
        if not token:
            return None
        with self._lock:
            s = self._sessions.get(token)
            if not s:
                return None
            now = time.time()
            if now - s["created"] > SESSION_TTL or now - s["last"] > SESSION_IDLE:
                self._sessions.pop(token, None)
                return None
            s["last"] = now
            return s

    def destroy(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def destroy_user(self, user):
        """注销某个账号的所有会话（改密/删号/改授权后用）。"""
        with self._lock:
            dead = [k for k, s in self._sessions.items() if s.get("user") == user]
            for k in dead:
                self._sessions.pop(k, None)
            return len(dead)

    def destroy_all(self):
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
            return n

    def count(self):
        with self._lock:
            return len(self._sessions)

    def sweep(self):
        now = time.time()
        with self._lock:
            dead = [k for k, s in self._sessions.items()
                    if now - s["created"] > SESSION_TTL or now - s["last"] > SESSION_IDLE]
            for k in dead:
                self._sessions.pop(k, None)
            return len(dead)


class RateLimiter(object):
    """按 key 统计登录失败次数，1 分钟内超过 5 次即暂时拒绝。

    key 由调用方拼成 "ip|用户名"：同一台机器上多人用不同账号登录时，
    一个人的密码输错不会把别人一起锁掉。
    """

    def __init__(self, max_fails=MAX_FAILS_PER_MIN, window=60, block_for=60):
        self.max_fails = max_fails
        self.window = window
        self.block_for = block_for
        self._lock = threading.Lock()
        self._fails = {}     # key -> [时间戳]
        self._blocked = {}   # key -> 解除时间

    def allow(self, key):
        now = time.time()
        with self._lock:
            unblock = self._blocked.get(key)
            if unblock and now < unblock:
                return False, int(unblock - now) + 1
            if unblock:
                self._blocked.pop(key, None)
            ts = self._fails.get(key) or []
            ts = [t for t in ts if now - t < self.window]
            self._fails[key] = ts
            return True, 0

    def fail(self, key):
        now = time.time()
        with self._lock:
            ts = self._fails.get(key) or []
            ts = [t for t in ts if now - t < self.window]
            ts.append(now)
            self._fails[key] = ts
            if len(ts) >= self.max_fails:
                self._blocked[key] = now + self.block_for
                return True
            return False

    def reset(self, key):
        with self._lock:
            self._fails.pop(key, None)
            self._blocked.pop(key, None)


class CaptchaStore(object):
    """登录页验证码：独立于会话，一次性消费，5 分钟过期。"""

    def __init__(self, ttl=300, max_items=500):
        self.ttl = ttl
        self.max_items = max_items
        self._lock = threading.Lock()
        self._items = {}

    def put(self, code):
        cid = secrets.token_urlsafe(12)
        now = time.time()
        with self._lock:
            self._sweep(now)
            self._items[cid] = (code, now)
        return cid

    def take(self, cid):
        """取出并删除（一次性）。返回 code 或 None。"""
        if not cid:
            return None
        now = time.time()
        with self._lock:
            item = self._items.pop(cid, None)
        if not item:
            return None
        code, ts = item
        if now - ts > self.ttl:
            return None
        return code

    def _sweep(self, now):
        dead = [k for k, (c, t) in self._items.items() if now - t > self.ttl]
        for k in dead:
            self._items.pop(k, None)
        if len(self._items) > self.max_items:
            oldest = sorted(self._items.items(), key=lambda kv: kv[1][1])
            for k, _ in oldest[:len(self._items) - self.max_items]:
                self._items.pop(k, None)
