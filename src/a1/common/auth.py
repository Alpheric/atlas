"""Authentication, authorization, and rate limiting.

AuthContext carries the resolved identity (workspace, role, key hash) for
the current request. All downstream code uses this instead of raw API keys.
"""

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from a1.common.logging import get_logger
from a1.db.engine import async_session
from a1.db.models import ApiKey  # noqa: F401 — also used in _verify_key_in_db
from config.settings import settings

log = get_logger("auth")

security = HTTPBearer(auto_error=False)

_DEFAULT_RATE_LIMIT_RPM = 60
_WINDOW_SECONDS = 60


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class AuthContext:
    """Resolved identity for the current request."""

    api_key: str
    key_hash: str | None
    workspace_id: str | None  # None in dev mode or if key has no workspace
    role: str  # "admin", "developer", "viewer"
    user_id: str | None = None  # future: from SSO/OIDC


# Cache: key_hash -> (workspace_id, role, rate_limit, cached_at)
_key_cache: dict[str, tuple[str | None, str, int, float]] = {}
_KEY_CACHE_TTL = 300  # 5 minutes


async def _resolve_key_info(key_hash: str) -> tuple[str | None, str, int]:
    """Look up workspace_id, role, and rate_limit from DB for a key hash.

    Returns (workspace_id, role, rate_limit). Uses a 5-minute in-memory cache.
    """
    now = time.time()
    cached = _key_cache.get(key_hash)
    if cached and (now - cached[3]) < _KEY_CACHE_TTL:
        return cached[0], cached[1], cached[2]

    workspace_id = None
    role = "developer"
    rate_limit = _DEFAULT_RATE_LIMIT_RPM

    try:
        async with async_session() as session:
            result = await session.execute(
                select(
                    ApiKey.workspace_id,
                    ApiKey.role,
                    ApiKey.rate_limit,
                ).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
            )
            row = result.first()
            if row:
                workspace_id = str(row[0]) if row[0] else None
                role = row[1] or "developer"
                rate_limit = row[2] or _DEFAULT_RATE_LIMIT_RPM
    except Exception as e:
        log.debug(f"Failed to look up key info: {e}")

    _key_cache[key_hash] = (workspace_id, role, rate_limit, now)
    return workspace_id, role, rate_limit


# ---------------------------------------------------------------------------
# Rate limiting (Redis primary, in-memory fallback)
# ---------------------------------------------------------------------------

# In-memory token bucket fallback when Redis is unavailable
_mem_buckets: dict[str, list[float]] = defaultdict(list)


def _enforce_rate_limit_memory(key_hash: str, rate_limit: int) -> None:
    """In-memory sliding window rate limiter (fallback when Redis is down)."""
    now = time.time()
    window_start = now - _WINDOW_SECONDS
    bucket = _mem_buckets[key_hash]

    # Remove expired entries
    _mem_buckets[key_hash] = [t for t in bucket if t > window_start]
    bucket = _mem_buckets[key_hash]

    if len(bucket) >= rate_limit:
        retry_after = max(1, int(_WINDOW_SECONDS - (now - bucket[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({rate_limit} req/min). Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    bucket.append(now)


async def _enforce_rate_limit(key_hash: str, rate_limit: int) -> None:
    """Rate limit with Redis primary, in-memory fallback."""
    try:
        from a1.dependencies import get_redis

        r = await get_redis()

        redis_key = f"rate_limit:{key_hash}"
        now = time.time()
        window_start = now - _WINDOW_SECONDS
        member = str(time.time_ns())

        pipe = r.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {member: now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, _WINDOW_SECONDS + 1)
        results = await pipe.execute()

        current_count = results[2]
        if current_count > rate_limit:
            oldest = await r.zrange(redis_key, 0, 0, withscores=True)
            retry_after = _WINDOW_SECONDS
            if oldest:
                retry_after = max(1, int(_WINDOW_SECONDS - (now - oldest[0][1])) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({rate_limit} req/min). Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception:
        # Redis unavailable -- fall back to in-memory rate limiting
        _enforce_rate_limit_memory(key_hash, rate_limit)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def _verify_key_in_db(key_hash: str) -> bool:
    """Check if a key hash exists and is active in api_keys OR atlas_api_keys.

    api_keys       — dashboard/internal keys created via the UI
    atlas_api_keys — OneDesk tenant keys created via the provisioning API
    Both tables are valid; either table match authenticates the request.
    """
    try:
        async with async_session() as session:
            # 1. Dashboard api_keys table
            result = await session.execute(
                select(ApiKey.id).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
            )
            if result.first() is not None:
                return True

            # 2. OneDesk tenant keys (atlas_api_keys)
            from a1.db.models import AtlasApiKey
            result2 = await session.execute(
                select(AtlasApiKey.id).where(
                    AtlasApiKey.key_hash == key_hash,
                    AtlasApiKey.status == "active",
                )
            )
            return result2.first() is not None
    except Exception as e:
        log.debug(f"DB key check failed: {e}")
        return False


async def _update_key_last_used(key_hash: str) -> None:
    """Update last_used_at timestamp on the matching key record (either table)."""
    try:
        from sqlalchemy import update as sa_update

        from a1.common.tz import now_ist
        from a1.db.models import AtlasApiKey

        async with async_session() as session:
            async with session.begin():
                # Update api_keys
                await session.execute(
                    sa_update(ApiKey)
                    .where(ApiKey.key_hash == key_hash)
                    .values(last_used_at=now_ist())
                )
                # Also update atlas_api_keys (OneDesk tenant keys)
                await session.execute(
                    sa_update(AtlasApiKey)
                    .where(AtlasApiKey.key_hash == key_hash)
                    .values(
                        last_used_at=now_ist(),
                        requests_total=AtlasApiKey.requests_total + 1,
                    )
                )
    except Exception:
        pass  # non-critical


def _client_ip(request: Request | None) -> str:
    """Best-effort real client IP, accounting for Cloudflare / reverse-proxy headers.

    Order: cf-connecting-ip → first hop of X-Forwarded-For → request.client.host.
    """
    if request is None:
        return "-"
    headers = request.headers
    cf = headers.get("cf-connecting-ip")
    if cf:
        return cf
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "-"


def _log_invalid_key(request: Request | None, key: str, route: str) -> None:
    """WARN-level audit log for a rejected key attempt.

    Records only the first/last 4 chars of the key so the full secret isn't
    leaked into logs but operators can still correlate repeated probes.
    """
    ip = _client_ip(request)
    ua = (request.headers.get("user-agent", "-") if request is not None else "-")[:80]
    if key and len(key) >= 9:
        masked = f"{key[:4]}…{key[-4:]}"
    else:
        masked = "<empty>"
    log.warning(
        f"Invalid API key on {route} from ip={ip} key={masked} ua={ua}"
    )


async def verify_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> str:
    """Verify API key from Bearer token and enforce rate limits. Returns the raw key.

    Accepts keys from these sources (in priority order):
      1. settings.api_keys — env-var master keys (admin access, always accepted)
      2. settings.alpheric_ai_platform_api_key — OneDesk platform master key
      3. atlas_api_keys DB table — OneDesk tenant keys (created via provisioning API)
      4. api_keys DB table — per-user keys created via the dashboard
    """
    if not settings.api_keys:
        return "dev"

    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing API key")

    key = credentials.credentials
    key_h = hash_key(key)

    # 1. Env-var master keys (admin)
    if key in settings.api_keys:
        _, _, rate_limit = await _resolve_key_info(key_h)
        await _enforce_rate_limit(key_h, rate_limit)
        return key

    # 2. OneDesk platform master key
    if settings.alpheric_ai_platform_api_key and key == settings.alpheric_ai_platform_api_key:
        _, _, rate_limit = await _resolve_key_info(key_h)
        await _enforce_rate_limit(key_h, rate_limit)
        return key

    # 3 & 4. DB keys (atlas_api_keys tenant keys + api_keys dashboard keys)
    if not await _verify_key_in_db(key_h):
        _log_invalid_key(request, key, str(request.url.path))
        raise HTTPException(status_code=403, detail="Invalid API key")

    # Valid DB key — update last_used timestamp in background
    import asyncio

    asyncio.create_task(_update_key_last_used(key_h))

    _, _, rate_limit = await _resolve_key_info(key_h)
    await _enforce_rate_limit(key_h, rate_limit)

    return key


async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> AuthContext:
    """Full auth resolution: verify key, resolve workspace and role.

    Use this instead of verify_api_key when you need workspace scoping.
    """
    if not settings.api_keys:
        return AuthContext(api_key="dev", key_hash=None, workspace_id=None, role="admin")

    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing API key")

    key = credentials.credentials
    if key not in settings.api_keys:
        _log_invalid_key(request, key, str(request.url.path))
        raise HTTPException(status_code=403, detail="Invalid API key")

    key_h = hash_key(key)
    workspace_id, role, rate_limit = await _resolve_key_info(key_h)
    await _enforce_rate_limit(key_h, rate_limit)

    return AuthContext(api_key=key, key_hash=key_h, workspace_id=workspace_id, role=role)
