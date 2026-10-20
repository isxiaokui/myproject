import os
import threading
import time

from fastapi import HTTPException, Request

from app.cache import cache_increment, cache_key

_lock = threading.Lock()
_windows: dict[str, tuple[int, int]] = {}


def _memory_increment(key: str, window_seconds: int) -> int:
    now = int(time.time())
    with _lock:
        count, expires_at = _windows.get(key, (0, now + window_seconds))
        if now >= expires_at:
            count, expires_at = 0, now + window_seconds
        count += 1
        _windows[key] = (count, expires_at)
        if len(_windows) > 10_000:
            for old_key, (_, expiry) in list(_windows.items()):
                if expiry <= now:
                    _windows.pop(old_key, None)
        return count


def enforce_auth_rate_limit(request: Request, action: str) -> None:
    address = request.client.host if request.client else "unknown"
    window = max(1, int(os.getenv("AUTH_RATE_WINDOW_SECONDS", "60")))
    default_limit = "10" if action == "login" else "5"
    limit = max(1, int(os.getenv(f"AUTH_{action.upper()}_RATE_LIMIT", default_limit)))
    key = cache_key("auth-rate", [action, address, int(time.time()) // window])
    count = cache_increment(key, window) or _memory_increment(key, window)
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试。",
            headers={"Retry-After": str(window)},
        )


def enforce_resource_rate_limit(request: Request, action: str, user_id: str) -> None:
    address = request.client.host if request.client else "unknown"
    window = max(1, int(os.getenv("RESOURCE_RATE_WINDOW_SECONDS", "60")))
    defaults = {
        "chat": {"ip": 30, "user": 10},
        "image": {"ip": 10, "user": 5},
    }
    if action not in defaults:
        raise ValueError(f"Unknown rate limit action: {action}")

    for scope, identity in (("ip", address), ("user", user_id)):
        default_limit = defaults[action][scope]
        limit = max(
            1,
            int(os.getenv(f"{action.upper()}_{scope.upper()}_RATE_LIMIT", str(default_limit))),
        )
        key = cache_key(
            "resource-rate",
            [action, scope, identity, int(time.time()) // window],
        )
        count = cache_increment(key, window) or _memory_increment(key, window)
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试。",
                headers={"Retry-After": str(window)},
            )
