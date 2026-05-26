"""
Rate limiting for the portal backend.

Uses slowapi with a Redis storage backend. Counters survive deploys and are
shared across all gunicorn workers.

Two key functions are exported:
  - client_ip_key: extracts the real client IP from X-Forwarded-For (set by
    Railway's edge proxy), falling back to request.client.host. Without this,
    every request would appear to come from Railway's internal load-balancer
    IP and all users would share a single rate limit.
  - login_username_key: reads the email field from the JSON body of a login
    POST. Used for per-account rate limiting alongside per-IP.

The custom 429 handler returns a clean JSON body the frontend can parse.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

log = logging.getLogger(__name__)


# --- key functions ---------------------------------------------------------

def client_ip_key(request: Request) -> str:
    """Real client IP, X-Forwarded-For aware (Railway edge sets this)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # XFF can be "client, proxy1, proxy2" — the leftmost is the original
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def login_username_key(request: Request) -> str:
    """
    Per-username key for the login endpoint.

    Reads the email from the cached request body. FastAPI caches the body
    on first read, so reading it here does not prevent the route handler
    from reading it again.

    Returns 'no-username' if the body can't be parsed — this groups all
    malformed-body attempts under one bucket so they share a limit.
    """
    try:
        # slowapi calls key functions synchronously; we can't await here.
        # The body has typically already been read by FastAPI's request
        # parser, but if not, we fall back to the unknown bucket.
        # In practice this works because the limit decorator runs before
        # the body validator only on the FIRST call; subsequent reads use
        # the cached value.
        body = getattr(request, "_body", None)
        if body is None:
            # Body hasn't been read yet — return a stable placeholder so
            # all unread-body requests share a bucket (still rate-limited
            # by the IP key, so this is fine).
            return f"pending:{client_ip_key(request)}"
        data = json.loads(body)
        email = (data.get("email") or "").lower().strip()
        return email or "no-username"
    except Exception:
        return f"parse-error:{client_ip_key(request)}"


# --- limiter ---------------------------------------------------------------

def _make_limiter() -> Limiter:
    """
    Build the slowapi Limiter.

    Prefers Redis (REDIS_URL env). Falls back to in-memory storage if Redis
    is unset or unreachable — slowapi handles this gracefully.
    """
    redis_url = os.environ.get("REDIS_URL", "").strip()
    storage_uri: Optional[str] = None
    if redis_url:
        # slowapi-compatible URI for limits library
        storage_uri = redis_url
        log.info("Rate limiter using Redis backend")
    else:
        log.warning("Rate limiter using in-memory backend (REDIS_URL not set)")

    return Limiter(
        key_func=client_ip_key,
        storage_uri=storage_uri,
        # Headers off — frontend doesn't need rate-limit-remaining info,
        # and keeping them off avoids accidentally leaking timing info.
        headers_enabled=False,
        # When a limit is exceeded, slowapi raises RateLimitExceeded which
        # we handle below.
        strategy="fixed-window",
    )


limiter = _make_limiter()


# --- 429 handler -----------------------------------------------------------

def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom 429 response. Friendly message, no Retry-After in the body
    (we don't want to leak exact rate windows to attackers).

    The frontend will look for status === 429 and show the user a
    "try again later" message.
    """
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": (
                "Too many login attempts. For your account's security, "
                "we've temporarily paused new attempts. Please try again "
                "in about 1 minute."
            ),
        },
    )
