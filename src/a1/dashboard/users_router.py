"""User management API — CRUD for users, API key generation, and per-user usage stats."""

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.auth import hash_key
from a1.common.logging import get_logger
from a1.db.models import ApiKey, UsageRecord, User
from a1.dependencies import get_db

log = get_logger("dashboard.users")
router = APIRouter(prefix="/users", tags=["users"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    name: str
    email: str
    role: str = "developer"
    rate_limit: int = 60
    monthly_token_limit: int = 0  # 0 = unlimited


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    rate_limit: int | None = None
    monthly_token_limit: int | None = None
    is_active: bool | None = None


class KeyCreate(BaseModel):
    name: str
    rate_limit: int | None = None  # overrides user default if set
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_user_or_404(user_id: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _usage_stats(key_hashes: list[str], db: AsyncSession) -> dict:
    """Aggregate usage stats across a list of key hashes."""
    if not key_hashes:
        return {"total_requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    result = await db.execute(
        select(
            func.count(UsageRecord.id).label("total_requests"),
            func.coalesce(func.sum(UsageRecord.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageRecord.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost_usd"),
        ).where(UsageRecord.api_key_hash.in_(key_hashes))
    )
    row = result.first()
    return {
        "total_requests": row.total_requests or 0,
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "cost_usd": round(float(row.cost_usd or 0), 6),
    }


def _user_dict(user: User, keys: list[ApiKey], stats: dict) -> dict:
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "rate_limit": user.rate_limit,
        "monthly_token_limit": user.monthly_token_limit,
        "created_at": user.created_at.isoformat(),
        "key_count": len(keys),
        "active_key_count": sum(1 for k in keys if k.is_active),
        "usage": stats,
    }


def _key_dict(key: ApiKey, show_prefix: bool = True) -> dict:
    return {
        "id": str(key.id),
        "name": key.name,
        "role": key.role,
        "is_active": key.is_active,
        "rate_limit": key.rate_limit,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "created_at": key.created_at.isoformat(),
        # Show first 12 chars of hash as a visual identifier (not the real key)
        "key_prefix": f"sk-atlas-...{key.key_hash[-6:]}",
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db)):
    """List all users with key counts and aggregate usage stats."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()

    out = []
    for user in users:
        keys_result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
        keys = keys_result.scalars().all()
        key_hashes = [k.key_hash for k in keys]
        stats = await _usage_stats(key_hashes, db)
        out.append(_user_dict(user, keys, stats))

    return {"data": out, "total": len(out)}


@router.post("")
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create a new user."""
    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already exists")

    user = User(
        name=body.name,
        email=body.email,
        role=body.role,
        rate_limit=body.rate_limit,
        monthly_token_limit=body.monthly_token_limit,
    )
    db.add(user)
    await db.commit()
    log.info(f"Created user {user.email} ({user.id})")
    return _user_dict(  # noqa: E501
        user, [], {"total_requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    )


@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """Get user detail with keys and usage."""
    user = await _get_user_or_404(user_id, db)
    keys_result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
    keys = keys_result.scalars().all()
    key_hashes = [k.key_hash for k in keys]
    stats = await _usage_stats(key_hashes, db)
    return {
        **_user_dict(user, keys, stats),
        "keys": [_key_dict(k) for k in keys],
    }


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, db: AsyncSession = Depends(get_db)):
    """Update user fields."""
    user = await _get_user_or_404(user_id, db)
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        user.role = body.role
    if body.rate_limit is not None:
        user.rate_limit = body.rate_limit
    if body.monthly_token_limit is not None:
        user.monthly_token_limit = body.monthly_token_limit
    if body.is_active is not None:
        user.is_active = body.is_active
        # Cascade deactivation to all their keys
        if not body.is_active:
            await db.execute(
                update(ApiKey).where(ApiKey.user_id == user.id).values(is_active=False)
            )
    await db.commit()
    return {"ok": True, "user_id": str(user.id)}


@router.delete("/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """Deactivate a user and all their API keys."""
    user = await _get_user_or_404(user_id, db)
    user.is_active = False
    await db.execute(update(ApiKey).where(ApiKey.user_id == user.id).values(is_active=False))
    await db.commit()
    log.info(f"Deactivated user {user.email}")
    return {"ok": True}


@router.get("/{user_id}/usage")
async def get_user_usage(user_id: str, db: AsyncSession = Depends(get_db)):
    """Per-user usage: aggregate + per-key breakdown + recent requests."""
    user = await _get_user_or_404(user_id, db)
    keys_result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
    keys = keys_result.scalars().all()
    key_hashes = [k.key_hash for k in keys]

    # Aggregate stats
    stats = await _usage_stats(key_hashes, db)

    # Per-key stats
    key_stats = []
    for k in keys:
        ks = await _usage_stats([k.key_hash], db)
        key_stats.append({**_key_dict(k), "usage": ks})

    # Last 20 requests (most recent usage records)
    recent = []
    if key_hashes:
        recent_result = await db.execute(
            select(UsageRecord)
            .where(UsageRecord.api_key_hash.in_(key_hashes))
            .order_by(UsageRecord.created_at.desc())
            .limit(20)
        )
        for rec in recent_result.scalars().all():
            recent.append(
                {
                    "provider": rec.provider,
                    "model": rec.model,
                    "prompt_tokens": rec.prompt_tokens,
                    "completion_tokens": rec.completion_tokens,
                    "cost_usd": float(rec.cost_usd or 0),
                    "latency_ms": rec.latency_ms,
                    "is_local": rec.is_local,
                    "created_at": rec.created_at.isoformat(),
                }
            )

    return {
        "user_id": user_id,
        "aggregate": stats,
        "per_key": key_stats,
        "recent_requests": recent,
    }


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


@router.post("/{user_id}/keys")
async def create_api_key(user_id: str, body: KeyCreate, db: AsyncSession = Depends(get_db)):
    """Generate a new API key for a user. Returns the raw key ONCE — store it safely."""
    user = await _get_user_or_404(user_id, db)
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Cannot create key for inactive user")

    # Generate key: sk-atlas-<user_slug>-<random>
    slug = user.email.split("@")[0].replace(".", "-")[:12]
    raw_key = f"sk-atlas-{slug}-{secrets.token_urlsafe(24)}"
    key_hash = hash_key(raw_key)

    api_key = ApiKey(
        key_hash=key_hash,
        name=body.name,
        user_id=user.id,
        role=user.role,
        is_active=True,
        rate_limit=body.rate_limit if body.rate_limit is not None else user.rate_limit,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.commit()

    log.info(f"Created API key '{body.name}' for user {user.email}")

    # Return raw key only on creation — never stored, cannot be retrieved again
    return {
        "key_id": str(api_key.id),
        "name": api_key.name,
        "api_key": raw_key,  # ← show ONCE
        "key_prefix": f"sk-atlas-...{key_hash[-6:]}",
        "role": api_key.role,
        "rate_limit": api_key.rate_limit,
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
        "created_at": api_key.created_at.isoformat(),
        "warning": "Save this key now — it will not be shown again.",
    }


@router.delete("/{user_id}/keys/{key_id}")
async def revoke_api_key(user_id: str, key_id: str, db: AsyncSession = Depends(get_db)):
    """Revoke (deactivate) a specific API key."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == uuid.UUID(key_id),
            ApiKey.user_id == uuid.UUID(user_id),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    key.is_active = False
    await db.commit()
    log.info(f"Revoked key {key_id} for user {user_id}")
    return {"ok": True, "key_id": key_id}


@router.patch("/{user_id}/keys/{key_id}")
async def toggle_api_key(
    user_id: str,
    key_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable a specific API key."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == uuid.UUID(key_id),
            ApiKey.user_id == uuid.UUID(user_id),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    if "is_active" in body:
        key.is_active = body["is_active"]
    await db.commit()
    return {"ok": True}
