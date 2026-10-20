import hashlib
import json
import logging
import os
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
_client: Redis | None = None


def init_cache() -> None:
    global _client
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
    if not url:
        return

    client = Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        client.ping()
    except RedisError as exc:
        client.close()
        logger.warning(
            "Redis cache is unavailable; continuing without cache (%s)",
            type(exc).__name__,
        )
        return
    _client = client


def close_cache() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def cache_key(namespace: str, value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"flower-assistant:v1:{namespace}:{digest}"


def cache_get(key: str) -> Any | None:
    if _client is None:
        return None
    try:
        value = _client.get(key)
        return json.loads(value) if value is not None else None
    except json.JSONDecodeError:
        return None
    except RedisError as exc:
        _disable_cache(exc)
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    if _client is None:
        return
    try:
        _client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
    except RedisError as exc:
        _disable_cache(exc)


def cache_increment(key: str, ttl_seconds: int) -> int | None:
    if _client is None:
        return None
    try:
        pipe = _client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)
        result = pipe.execute()
        return int(result[0])
    except RedisError as exc:
        _disable_cache(exc)
        return None


def _disable_cache(error: RedisError) -> None:
    global _client
    client = _client
    _client = None
    if client is not None:
        client.close()
    logger.warning(
        "Redis cache disabled after an error; continuing without cache (%s)",
        type(error).__name__,
    )
