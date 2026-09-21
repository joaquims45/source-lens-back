import pickle
from functools import lru_cache
from typing import Any

import redis

from sourcelens.config import Settings


@lru_cache
def _client_for(redis_url: str) -> "redis.Redis[bytes]":
    return redis.Redis.from_url(redis_url)


def get_client(settings: Settings) -> "redis.Redis[bytes]":
    return _client_for(settings.redis_url)


def cache_get(settings: Settings, key: str) -> Any | None:
    """Pickle-based, not JSON: this caches internal dataclasses (e.g. the
    architecture graph) exactly as computed, with no separate schema to keep
    in sync. Values only ever come from our own `cache_set` calls, never
    from repository content or other untrusted input, so unpickling them
    back is safe.
    """
    raw = get_client(settings).get(key)
    return pickle.loads(raw) if raw is not None else None


def cache_set(settings: Settings, key: str, value: Any, ttl_seconds: int) -> None:
    get_client(settings).set(key, pickle.dumps(value), ex=ttl_seconds)
