# -*- coding: utf-8 -*-
"""Redis 面板（移植自 sqlweb v2.3，保持行为一致）。"""
import json
import queue
import shlex
import socket
import threading
import time

class RedisError(Exception):
    """Redis 服务端返回的错误回复"""


def _redis_encode(args):
    parts = [b"*%d\r\n" % len(args)]
    for a in args:
        b = a if isinstance(a, bytes) else str(a).encode("utf-8")
        parts.append(b"$%d\r\n" % len(b))
        parts.append(b)
        parts.append(b"\r\n")
    return b"".join(parts)


class RedisClient:
    """极简 RESP2 客户端：连接、AUTH、SELECT、命令、流水线、回复解析。"""

    def __init__(self, host, port=6379, db=0, password=None, username=None, timeout=5):
        self.host = host
        self.port = int(port or 6379)
        self.db = int(db or 0)
        self.password = password
        self.username = username
        self.timeout = float(timeout or 5)
        self.sock = None
        self.fp = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)
        self.fp = self.sock.makefile("rb")
        if self.password:
            if self.username:
                self.execute("AUTH", self.username, self.password)
            else:
                self.execute("AUTH", self.password)
        if self.db:
            self.execute("SELECT", self.db)
        return self

    def close(self):
        for obj in (self.fp, self.sock):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self.fp = None
        self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _readline(self):
        line = self.fp.readline()
        if not line:
            raise RedisError("连接已被服务端关闭")
        return line.rstrip(b"\r\n")

    def _read_reply(self):
        line = self._readline()
        if not line:
            raise RedisError("收到空回复")
        t, body = line[:1], line[1:]
        if t == b"+":
            return body.decode("utf-8", "replace")
        if t == b"-":
            raise RedisError(body.decode("utf-8", "replace"))
        if t == b":":
            return int(body)
        if t == b"$":
            n = int(body)
            if n == -1:
                return None
            data = self.fp.read(n + 2)
            if data is None or len(data) < n + 2:
                raise RedisError("读取批量字符串时连接中断")
            return data[:-2].decode("utf-8", "replace")
        if t == b"*":
            n = int(body)
            if n == -1:
                return None
            return [self._read_reply() for _ in range(n)]
        raise RedisError("无法解析的回复类型: %r" % line[:24])

    def execute(self, *args):
        self.sock.sendall(_redis_encode(args))
        return self._read_reply()

    def execute_pipeline(self, commands):
        """
        一次发多条命令再统一读回复。用于批量取 TYPE/TTL，避免 N 次往返。
        单条出错不中断整批（返回 Exception 占位），保证后续回复不错位。
        """
        if not commands:
            return []
        self.sock.sendall(b"".join(_redis_encode(c) for c in commands))
        out = []
        for _ in commands:
            try:
                out.append(self._read_reply())
            except RedisError as e:
                out.append(e)
        return out


# 只读命令白名单。写命令（set/del/expire/hset/lpush/sadd/zadd/rename/flushdb…）
# 不在表里，只读模式下直接拒绝。
# 注：acl / config / client / script / eval 一律不给 —— 它们的子命令能改权限、改参数、
# 执行任意 Lua，风险远大于收益。
REDIS_READONLY_CMDS = frozenset("""
get mget strlen getrange type ttl pttl exists scan keys dbsize randomkey
object memory info ping echo time lastsave dump
hget hmget hgetall hkeys hvals hlen hexists hrandfield hscan hstrlen
llen lrange lindex lpos
scard sismember smembers srandmember sscan smismember
zcard zscore zrange zrevrange zrangebyscore zcount zscan zrank zrevrank zmscore
xinfo xlen xrange xrevrange
geopos geodist geosearch georadius_ro
pfcount bitcount getbit bitpos
command
""".split())

# 常见写命令，仅用于把报错文案说准（"这是写命令" vs "不在白名单"）
REDIS_WRITE_CMDS = frozenset("""
set setnx setex psetex append getset getdel getex mset msetnx
del unlink expire pexpire expireat pexpireat persist rename renamenx move copy
incr decr incrby decrby incrbyfloat decrbyfloat hincrby hincrbyfloat
hset hsetnx hmset hdel lpush rpush lpushx rpushx lpop rpop linsert lset
lrem ltrim lmove rpoplpush blpop brpop blmove brpoplpush
sadd srem spop smove zadd zincrby zrem zremrangebyrank zremrangebyscore
zremrangebylex zpopmin zpopmax bzpopmin bzpopmax zunionstore zinterstore zdiffstore
xadd xdel xtrim xgroup xack xclaim xautoclaim
pfadd pfmerge geoadd georadius georadiusbymember
flushdb flushall swapdb shutdown slaveof replicaof failover
migrate restore sort eval evalsha eval_ro evalsha_ro fcall fcall_ro script
multi exec discard watch unwatch publish subscribe unsubscribe
psubscribe punsubscribe wait debug module config acl client
""".split())


# ---------------------------------------------------------------- 哨兵（Sentinel）
# 哨兵模式下数据源里不存 master 的真实地址，每次现问哨兵要，故障转移后自然跟上。
# 问一次要走哨兵一个往返（几毫秒），所以解析结果按实例 key 缓存 5 秒，避免刷 key 列表时反复问。
_SENTINEL_CACHE = {}
_SENTINEL_TTL = 5.0
_SENTINEL_TIMEOUT = 3.0


def parse_sentinel_nodes(values):
    """把哨兵节点规整成 [(host, port)]。支持多行、"a:1,b:2"、带 redis:// 前缀、省略端口默认 26379。"""
    if isinstance(values, str):
        values = values.replace(",", "\n").replace(";", "\n").replace(" ", "\n").splitlines()
    out = []
    for raw in (values or []):
        s = str(raw or "").strip()
        if not s:
            continue
        if "://" in s:
            s = s.split("://", 1)[1]
        s = s.split("/", 1)[0]
        host, _, port = s.rpartition(":") if ":" in s else (s, "", "")
        host = (host or "").strip()
        try:
            p = int(port or 26379)
        except ValueError:
            p = 26379
        if host:
            out.append((host, p))
    return out


def sentinel_master_addr(inst):
    """向哨兵要 master 地址，并确认它真的是 master。

    返回 (host, port, 探测日志)。哨兵全部不可用、或解析出的节点角色不是 master 时抛 RedisError ——
    故障转移中间态下宁可报错让用户重试，也不要把一个从库当 master 用（从库会拒写）。
    """
    name = str(inst.get("master_name") or "mymaster").strip() or "mymaster"
    pwd = inst.get("password") or None
    user = inst.get("username") or None
    timeout = min(float(inst.get("timeout") or 5), _SENTINEL_TIMEOUT)
    nodes = parse_sentinel_nodes(inst.get("sentinels"))
    if not nodes:
        raise RedisError("哨兵模式下没有可用的哨兵节点（应填 host:port，如 10.8.18.27:26379）")
    log, addr = [], None
    # 哨兵并发问：串行遇到网段不通要逐个吃满超时（3 个哨兵就是 9 秒），
    # 并发后总耗时约等于单节点一个超时，谁先给出地址就用谁。
    q = queue.Queue()
    jobs = []

    def ask(h, p):
        for use_auth in (False, True):
            if use_auth and not pwd:
                return q.put((None, "%s:%s 需要口令但没配口令" % (h, p)))
            client = None
            try:
                client = RedisClient(h, p, 0, pwd if use_auth else None,
                                     user if use_auth else None, timeout).connect()
                got = client.execute("SENTINEL", "get-master-addr-by-name", name)
                if got and len(got) >= 2:
                    return q.put((got, "%s:%s%s 给出 %s → %s:%s"
                                  % (h, p, "（带口令）" if use_auth else "", name, got[0], got[1])))
                return q.put((None, "%s:%s 不认识 master「%s」" % (h, p, name)))
            except Exception as e:
                msg = str(e).replace("\n", " ")[:80]
                # 免密被 NOAUTH 拒掉时改带口令再问一次（connect() 本身不发 AUTH，必须真执行才发现）
                if (not use_auth) and pwd and "NOAUTH" in msg.upper():
                    continue
                return q.put((None, "%s:%s 连不上/拒绝（%s）" % (h, p, msg)))
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass

    for h, p in nodes:
        t = threading.Thread(target=ask, args=(h, p), daemon=True)
        t.start()
        jobs.append(t)
    deadline = time.time() + timeout + 0.6
    got = 0
    while got < len(jobs):
        left = deadline - time.time()
        if left <= 0:
            break
        try:
            a, line = q.get(timeout=left)
        except queue.Empty:
            break
        got += 1
        log.append(line)
        if a:
            addr = a
            break
    if not addr:
        raise RedisError("哨兵没给出「%s」的 master 地址；探测过程：%s"
                         % (name, "；".join(log) or "无"))
    host, port = str(addr[0]), int(addr[1])
    # 角色校验也用短超时：哨兵答了但节点连不上时，没必要让它等满整个超时
    probe = RedisClient(host, port, 0, pwd, user, min(timeout, 2.5))
    try:
        probe.connect()
        role = probe.execute("ROLE") or []
    except Exception as e:
        raise RedisError("连哨兵给的 master %s:%s 失败：%s" % (host, port, str(e).replace("\n", " ")[:100]))
    finally:
        try:
            probe.close()
        except Exception:
            pass
    role_name = str(role[0]).lower() if role else ""
    if role_name and role_name != "master":
        raise RedisError("哨兵指向的 %s:%s 当前角色是 %s（可能正在故障转移），稍后重试"
                         % (host, port, role_name))
    log.append("master %s:%s 角色确认 = %s" % (host, port, role_name or "unknown"))
    return host, port, log


def redis_conn(inst):
    if str(inst.get("mode") or "standalone").lower() == "sentinel":
        ck = str(inst.get("key") or "") + "|" + str(inst.get("master_name") or "")
        hit = _SENTINEL_CACHE.get(ck)
        if hit and (time.time() - hit[2]) < _SENTINEL_TTL:
            host, port = hit[0], hit[1]
        else:
            host, port, _ = sentinel_master_addr(inst)
            _SENTINEL_CACHE[ck] = (host, port, time.time())
    else:
        host, port = inst["host"], inst["port"]
    return RedisClient(host, port, inst["db"],
                       inst.get("password"), inst.get("username"), inst.get("timeout", 5))


def redis_jsonable(v):
    if isinstance(v, list):
        return [redis_jsonable(x) for x in v]
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def redis_overview(inst):
    conn_mode = str(inst.get("mode") or "standalone").lower()
    info = {"instance": inst["key"], "host": inst["host"], "port": inst["port"],
            "db": inst["db"], "conn_mode": conn_mode}
    if conn_mode == "sentinel":
        info["master_name"] = inst.get("master_name") or ""
        info["sentinels"] = ["%s:%s" % (h, p) for h, p in parse_sentinel_nodes(inst.get("sentinels"))]
    with redis_conn(inst) as c:
        info["resolved"] = "%s:%s" % (c.host, c.port)
        for cmd in (("PING",),):
            c.execute(*cmd)
        raw = c.execute("INFO") or ""
        d = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()

        info["version"] = d.get("redis_version", "")
        info["mode"] = d.get("redis_mode", "")
        info["role"] = d.get("role", "")
        info["uptime_days"] = d.get("uptime_in_days", "")
        info["connected_clients"] = d.get("connected_clients", "")
        info["used_memory_human"] = d.get("used_memory_human", "")
        info["used_memory_peak_human"] = d.get("used_memory_peak_human", "")
        info["maxmemory_human"] = d.get("maxmemory_human", "")
        info["maxmemory_policy"] = d.get("maxmemory_policy", "")
        info["total_commands_processed"] = d.get("total_commands_processed", "")
        info["keyspace_hits"] = d.get("keyspace_hits", "")
        info["keyspace_misses"] = d.get("keyspace_misses", "")
        info["expired_keys"] = d.get("expired_keys", "")
        info["evicted_keys"] = d.get("evicted_keys", "")

        ks = {}
        for k, v in d.items():
            if k.startswith("db") and k[2:].isdigit():
                kv = {}
                for pair in v.split(","):
                    if "=" in pair:
                        a, b = pair.split("=", 1)
                        kv[a] = b
                ks[k] = kv
        info["keyspace"] = ks

        try:
            info["dbsize"] = c.execute("DBSIZE")
        except Exception:
            info["dbsize"] = None
        cur = ks.get("db%d" % inst["db"], {})
        info["keys"] = cur.get("keys", "")
        info["expires"] = cur.get("expires", "")
    return info


def _scan_collect(c, cmd, key, limit, count=100, max_rounds=30):
    """按 SCAN 家族逐轮收集，凑够 limit 或游标归零就停。"""
    items, cur, rounds = [], "0", 0
    while True:
        r = c.execute(cmd, key, cur, "COUNT", count)
        cur = r[0]
        items.extend(r[1] or [])
        rounds += 1
        if cur == "0" or len(items) >= limit * 2 or rounds >= max_rounds:
            break
    return items


def redis_scan(inst, pattern="*", cursor="0", count=None):
    count = max(1, min(int(count or 200), 500))
    with redis_conn(inst) as c:
        r = c.execute("SCAN", cursor, "MATCH", pattern or "*", "COUNT", count)
        nxt = int(r[0])
        keys = r[1] or []
        if not keys:
            return {"keys": [], "cursor": nxt, "done": nxt == 0}
        cmds = [["TYPE", k] for k in keys] + [["TTL", k] for k in keys]
        res = c.execute_pipeline(cmds)
        types = res[:len(keys)]
        ttls = res[len(keys):]
        out = []
        for i, k in enumerate(keys):
            t = types[i] if not isinstance(types[i], Exception) else "?"
            ttl = ttls[i] if not isinstance(ttls[i], Exception) else -1
            out.append({"key": k, "type": t, "ttl": ttl})
        return {"keys": out, "cursor": nxt, "done": nxt == 0}


def redis_key_detail(inst, key, limit=None):
    limit = max(1, min(int(limit or 200), 1000))
    with redis_conn(inst) as c:
        t = c.execute("TYPE", key)
        if t == "none":
            return None
        ttl = c.execute("TTL", key)
        enc = None
        try:
            enc = c.execute("OBJECT", "ENCODING", key)
        except Exception:
            pass

        d = {"key": key, "type": t, "ttl": ttl, "encoding": enc,
             "total": 0, "items": [], "truncated": False}

        if t == "string":
            size = c.execute("STRLEN", key)
            d["total"] = size
            if size > 4096:
                d["value"] = c.execute("GETRANGE", key, 0, 4095)
                d["truncated"] = True
            else:
                d["value"] = c.execute("GET", key) or ""
        elif t == "list":
            size = c.execute("LLEN", key)
            d["total"] = size
            items = c.execute("LRANGE", key, 0, limit - 1) or []
            d["items"] = [[i, v] for i, v in enumerate(items)]
            d["truncated"] = size > limit
        elif t == "set":
            size = c.execute("SCARD", key)
            d["total"] = size
            items = _scan_collect(c, "SSCAN", key, limit)
            d["items"] = items[:limit]
            d["truncated"] = size > limit
        elif t == "hash":
            size = c.execute("HLEN", key)
            d["total"] = size
            flat = _scan_collect(c, "HSCAN", key, limit)
            flat = flat[:limit * 2]
            d["items"] = [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]
            d["truncated"] = size > limit
        elif t == "zset":
            size = c.execute("ZCARD", key)
            d["total"] = size
            flat = _scan_collect(c, "ZSCAN", key, limit)
            flat = flat[:limit * 2]
            d["items"] = [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]
            d["truncated"] = size > limit
        elif t == "stream":
            size = c.execute("XLEN", key)
            d["total"] = size
            d["items"] = c.execute("XRANGE", key, "-", "+", "COUNT", limit) or []
            d["truncated"] = size > limit
        else:
            d["note"] = "该类型暂不支持在页面上展开"
        return d


def redis_run(inst, argv, readonly=True):
    name = str(argv[0]).strip().lower() if argv else ""
    if not name:
        return {"ok": False, "error": {"message": "命令为空", "stage": "parse"}, "elapsed_ms": 0}

    if readonly and name not in REDIS_READONLY_CMDS:
        if name in REDIS_WRITE_CMDS:
            msg = ("只读模式已开启，拒绝执行 Redis 写命令 %s。\n"
                   "如需写入，启动时加 --readwrite，或把 config.json 里该实例的 readonly 改为 false。"
                   % name.upper())
        else:
            msg = ("只读模式只放行白名单内的读命令，%s 不在白名单。\n"
                   "常见可用：GET / MGET / TYPE / TTL / SCAN / KEYS / INFO / DBSIZE / "
                   "HGETALL / LRANGE / SMEMBERS / ZRANGE / XRANGE / MEMORY USAGE"
                   % name.upper())
        return {
            "ok": False,
            "error": {"message": msg, "stage": "readonly"},
            "elapsed_ms": 0,
        }

    started = time.time()
    try:
        with redis_conn(inst) as c:
            r = c.execute(*argv)
        return {"ok": True, "data": redis_jsonable(r),
                "elapsed_ms": round((time.time() - started) * 1000, 1)}
    except RedisError as e:
        return {"ok": False, "error": {"message": str(e), "stage": "execute"},
                "elapsed_ms": round((time.time() - started) * 1000, 1)}
    except Exception as e:
        return {"ok": False, "error": {"message": "%s: %s" % (type(e).__name__, e), "stage": "connect"},
                "elapsed_ms": round((time.time() - started) * 1000, 1)}


