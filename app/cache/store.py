import threading
import time

import logfire

from app.config import settings


class MemoryStore:


    kind = "memory"

    def __init__(self) -> None:
        self._d: dict[str, object] = {}
        self._exp: dict[str, float] = {}
        self._lock = threading.RLock()

    def _alive(self, key: str) -> bool:
        exp = self._exp.get(key)
        if exp is not None and exp <= time.monotonic():
            self._d.pop(key, None)
            self._exp.pop(key, None)
            return False
        return key in self._d

    def get(self, key):
        with self._lock:
            return self._d[key] if self._alive(key) else None

    def set(self, key, value, ex=None):
        with self._lock:
            self._d[key] = value
            if ex:
                self._exp[key] = time.monotonic() + ex
            else:
                self._exp.pop(key, None)

    def incr(self, key):
        with self._lock:
            v = int(self._d[key]) + 1 if self._alive(key) else 1
            self._d[key] = str(v)
            return v

    def delete(self, *keys):
        with self._lock:
            for k in keys:
                self._d.pop(k, None)
                self._exp.pop(k, None)

    def expire(self, key, seconds):
        with self._lock:
            if self._alive(key):
                self._exp[key] = time.monotonic() + seconds

    # lists
    def rpush(self, key, value):
        with self._lock:
            if not self._alive(key):
                self._d[key] = []
            self._d[key].append(value)

    def lpush(self, key, value):
        with self._lock:
            if not self._alive(key):
                self._d[key] = []
            self._d[key].insert(0, value)

    def rpop(self, key):
        with self._lock:
            lst = self._d[key] if self._alive(key) else []
            return lst.pop() if lst else None

    def ltrim_last(self, key, n):
        """Keep only the last ``n`` items."""
        with self._lock:
            if self._alive(key):
                self._d[key] = self._d[key][-n:]

    def lrange_all(self, key):
        with self._lock:
            return list(self._d[key]) if self._alive(key) else []

    # hashes
    def hset(self, name, field, value):
        with self._lock:
            if not self._alive(name):
                self._d[name] = {}
            self._d[name][field] = value

    def hget(self, name, field):
        with self._lock:
            return self._d[name].get(field) if self._alive(name) else None

    def hgetall(self, name):
        with self._lock:
            return dict(self._d[name]) if self._alive(name) else {}

    def hdel(self, name, *fields):
        with self._lock:
            if self._alive(name):
                for f in fields:
                    self._d[name].pop(f, None)

    def hlen(self, name):
        with self._lock:
            return len(self._d[name]) if self._alive(name) else 0

    def ping(self):
        return True


class RedisStore:
    kind = "redis"

    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        self._r.ping()  # fail fast so get_store() can fall back

    def get(self, key):
        return self._r.get(key)

    def set(self, key, value, ex=None):
        self._r.set(key, value, ex=ex)

    def incr(self, key):
        return int(self._r.incr(key))

    def delete(self, *keys):
        if keys:
            self._r.delete(*keys)

    def expire(self, key, seconds):
        self._r.expire(key, seconds)

    def rpush(self, key, value):
        self._r.rpush(key, value)

    def lpush(self, key, value):
        self._r.lpush(key, value)

    def rpop(self, key):
        return self._r.rpop(key)

    def ltrim_last(self, key, n):
        self._r.ltrim(key, -n, -1)

    def lrange_all(self, key):
        return self._r.lrange(key, 0, -1)

    def hset(self, name, field, value):
        self._r.hset(name, field, value)

    def hget(self, name, field):
        return self._r.hget(name, field)

    def hgetall(self, name):
        return self._r.hgetall(name)

    def hdel(self, name, *fields):
        if fields:
            self._r.hdel(name, *fields)

    def hlen(self, name):
        return int(self._r.hlen(name))

    def ping(self):
        return self._r.ping()


_store = None
_lock = threading.Lock()


def get_store():
    """Process-wide store singleton (Redis if configured+reachable, else memory)."""
    global _store
    if _store is not None:
        return _store
    with _lock:
        if _store is None:
            if settings.REDIS_URL:
                try:
                    _store = RedisStore(settings.REDIS_URL)
                    logfire.info("Cache/conversation store: Redis")
                except Exception as e:  # noqa: BLE001
                    logfire.warning(f"Redis unavailable ({e}); falling back to in-memory store.")
                    _store = MemoryStore()
            else:
                logfire.info("REDIS_URL not set — using per-process in-memory store.")
                _store = MemoryStore()
    return _store


def set_store(store) -> None:
    """Override the singleton (tests / custom wiring)."""
    global _store
    _store = store
