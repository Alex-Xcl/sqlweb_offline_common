# -*- coding: utf-8 -*-
"""
认证与会话 —— sqlweb_offline_common

设计目标：
    1. 强制登录：没有 auth.json 时服务进入"初始化模式"，必须先设管理员口令
    2. 口令不落明文：PBKDF2-HMAC-SHA256 + 随机 salt（20 万次迭代）
    3. 会话用随机 token，放 httpOnly + SameSite=Strict 的 Cookie
    4. 登录失败按 IP 限速，防暴力破解
    5. 首次初始化需要一个控制台打印的一次性令牌，防止被别人抢先设密码

对外：
    AuthStore(path)      .initialized / set_password / verify / change_password
    SessionManager()     .create / get / destroy / sweep
    RateLimiter()        .allow / fail / reset
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
    """管理员口令存储。文件只保存 salt + hash，不保存明文。"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = None
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            self._data = None

    @property
    def initialized(self):
        d = self._data or {}
        return bool(d.get("user") and d.get("salt") and d.get("hash"))

    @property
    def user(self):
        return (self._data or {}).get("user") or "admin"

    def set_password(self, user, password):
        if not user or not user.strip():
            raise ValueError("用户名不能为空")
        if not password or len(password) < 8:
            raise ValueError("口令至少 8 位")
        salt = secrets.token_bytes(16)
        dk = _pbkdf2(password, salt)
        data = {
            "user": user.strip(),
            "salt": _hex(salt),
            "hash": _hex(dk),
            "iterations": PBKDF2_ITER,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self._data = data
        return True

    def verify(self, user, password):
        d = self._data or {}
        if not self.initialized:
            return False
        if user != d["user"]:
            # 用户名也做一次假校验，避免时序差异泄露账号是否存在
            _pbkdf2(password or "x", bytes.fromhex(d["salt"]))
            return False
        salt = bytes.fromhex(d["salt"])
        expect = bytes.fromhex(d["hash"])
        got = _pbkdf2(password or "", salt, int(d.get("iterations") or PBKDF2_ITER))
        return hmac.compare_digest(got, expect)

    def change_password(self, old, new):
        if not self.verify(self.user, old):
            raise ValueError("原口令不正确")
        return self.set_password(self.user, new)


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

    def destroy_all(self):
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
            return n

    def sweep(self):
        now = time.time()
        with self._lock:
            dead = [k for k, s in self._sessions.items()
                    if now - s["created"] > SESSION_TTL or now - s["last"] > SESSION_IDLE]
            for k in dead:
                self._sessions.pop(k, None)
            return len(dead)


class RateLimiter(object):
    """按 IP 统计登录失败次数，1 分钟内超过 5 次即暂时拒绝。"""

    def __init__(self, max_fails=MAX_FAILS_PER_MIN, window=60, block_for=60):
        self.max_fails = max_fails
        self.window = window
        self.block_for = block_for
        self._lock = threading.Lock()
        self._fails = {}     # ip -> [时间戳]
        self._blocked = {}   # ip -> 解除时间

    def allow(self, ip):
        now = time.time()
        with self._lock:
            unblock = self._blocked.get(ip)
            if unblock and now < unblock:
                return False, int(unblock - now) + 1
            if unblock:
                self._blocked.pop(ip, None)
            ts = self._fails.get(ip) or []
            ts = [t for t in ts if now - t < self.window]
            self._fails[ip] = ts
            return True, 0

    def fail(self, ip):
        now = time.time()
        with self._lock:
            ts = self._fails.get(ip) or []
            ts = [t for t in ts if now - t < self.window]
            ts.append(now)
            self._fails[ip] = ts
            if len(ts) >= self.max_fails:
                self._blocked[ip] = now + self.block_for
                return True
            return False

    def reset(self, ip):
        with self._lock:
            self._fails.pop(ip, None)
            self._blocked.pop(ip, None)


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
