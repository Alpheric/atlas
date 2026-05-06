"""Provisioning API — platform-to-platform endpoints for OneDesk.

All endpoints require Authorization: Bearer <ALPHERIC_AI_PLATFORM_API_KEY>.
The platform key is for provisioning ONLY — never for chat/completion.
Tenant keys created here are used for /v1/chat/completions.

Endpoints:
  POST /api/provision/tenant       — create or idempotently return tenant key
  POST /api/provision/rotate-key   — revoke old key, issue new one
  POST /api/provision/disable-key  — mark key disabled
  POST /api/provision/key-status   — return metadata (never the raw key)

Security:
  - Platform key validated via constant-time comparison (hmac.compare_digest)
  - Raw keys never stored, never logged
  - Every action produces an audit log row
  - IP and user-agent are hashed before storage
  - Rate limited: PROVISION_RATE_LIMIT_RPM per IP (default 10)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.logging import get_logger
from a1.common.tz import now_ist
from a1.db.engine import async_session
from a1.db.models import AtlasApiKey, ProvisioningAuditLog, UsageRecord
from config.settings import settings

log = get_logger("provisioning")

router = APIRouter(prefix="/api/provision", tags=["provisioning"])

# ── Rate limiter (in-memory sliding window, per client IP) ────────────────────

_prov_buckets: dict[str, list[float]] = defaultdict(list)
_PROV_WINDOW = 60  # seconds


def _rate_limit_ip(ip: str) -> None:
    limit = settings.provision_rate_limit_rpm
    now = time.time()
    bucket = _prov_buckets[ip]
    _prov_buckets[ip] = [t for t in bucket if t > now - _PROV_WINDOW]
    if len(_prov_buckets[ip]) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Too many provisioning requests. Limit: {limit}/min.",
            headers={"Retry-After": "60"},
        )
    _prov_buckets[ip].append(now)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hash(value: str) -> str:
    """SHA-256 hex digest — used for keys, IPs, and user-agents."""
    return hashlib.sha256(value.encode()).hexdigest()


def _verify_platform_key(authorization: str | None) -> None:
    """Validate the platform bearer token. Raises 401 on failure."""
    expected = settings.alpheric_ai_platform_api_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Provisioning not configured on this server.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    token = authorization[7:]  # strip "Bearer "
    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid platform API key.")


def _generate_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, key_prefix).

    raw_key    — returned to caller once, never stored
    key_hash   — SHA-256 stored in DB
    key_prefix — first 18 chars for display
    """
    prefix = settings.atlas_api_key_prefix  # "sk-atlas"
    token = secrets.token_urlsafe(32)  # 43 chars of URL-safe random
    raw_key = f"{prefix}-{token}"
    key_hash = _hash(raw_key)
    key_prefix = raw_key[:18]  # "sk-atlas-xxxxxxxxx" — safe to display
    return raw_key, key_hash, key_prefix


async def _audit(
    db: AsyncSession,
    *,
    tenant_id: str | None,
    alpheric_key_id: str | None,
    action: str,
    status: str,
    safe_message: str | None,
    request: Request,
) -> None:
    """Write one audit log row. Never logs raw keys or secrets."""
    client_ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    entry = ProvisioningAuditLog(
        tenant_id=tenant_id,
        alpheric_key_id=alpheric_key_id,
        action=action,
        status=status,
        safe_message=safe_message,
        request_id=request.headers.get("x-request-id"),
        ip_hash=_hash(client_ip),
        user_agent_hash=_hash(ua) if ua else None,
    )
    db.add(entry)
    await db.flush()


# ── Request / Response models ─────────────────────────────────────────────────


class ProvisionTenantRequest(BaseModel):
    tenant_id: str
    tenant_name: str
    tenant_owner_email: str
    source: str = "onedesk"
    force_new_key: bool = False

    @field_validator("tenant_id", "tenant_name", "tenant_owner_email")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


class RotateKeyRequest(BaseModel):
    tenant_id: str
    alpheric_key_id: str
    reason: str = "tenant_owner_requested"


class DisableKeyRequest(BaseModel):
    tenant_id: str
    alpheric_key_id: str
    reason: str = "tenant_disabled_provider"


class KeyStatusRequest(BaseModel):
    tenant_id: str
    alpheric_key_id: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/tenant")
async def provision_tenant(
    body: ProvisionTenantRequest,
    request: Request,
):
    """Create or idempotently return a tenant API key.

    Returns the raw key only when newly created. Existing keys are returned
    as metadata only (no raw key) unless force_new_key=True.
    """
    auth = request.headers.get("authorization")
    _verify_platform_key(auth)
    _rate_limit_ip(request.client.host if request.client else "unknown")

    async with async_session() as db:
        async with db.begin():
            # Check for existing active key for this tenant
            existing = (
                await db.execute(
                    select(AtlasApiKey)
                    .where(
                        AtlasApiKey.tenant_id == body.tenant_id,
                        AtlasApiKey.status == "active",
                    )
                    .order_by(AtlasApiKey.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if existing and not body.force_new_key:
                # Idempotent — return existing metadata without raw key
                await _audit(
                    db,
                    tenant_id=body.tenant_id,
                    alpheric_key_id=str(existing.id),
                    action="provisioned",
                    status="success",
                    safe_message="Returned existing active key metadata (no new key created).",
                    request=request,
                )
                return {
                    "success": True,
                    "alpheric_account_id": f"acct_{existing.tenant_id}",
                    "alpheric_key_id": str(existing.id),
                    "api_key": None,  # not returned for existing keys
                    "base_url": existing.base_url,
                    "default_model": existing.default_model,
                    "status": existing.status,
                    "created_at": existing.created_at.isoformat(),
                    "already_exists": True,
                }

            # If force_new_key, revoke existing keys first
            if existing and body.force_new_key:
                existing.status = "revoked"
                existing.revoked_at = now_ist()

            # Generate new key
            raw_key, key_hash, key_prefix = _generate_key()
            key_record = AtlasApiKey(
                tenant_id=body.tenant_id,
                tenant_name=body.tenant_name,
                tenant_owner_email=body.tenant_owner_email,
                source=body.source,
                key_prefix=key_prefix,
                key_hash=key_hash,
                status="active",
                default_model=settings.alpheric_ai_default_model,
                base_url=settings.alpheric_ai_base_url,
            )
            db.add(key_record)
            await db.flush()

            await _audit(
                db,
                tenant_id=body.tenant_id,
                alpheric_key_id=str(key_record.id),
                action="provisioned",
                status="success",
                safe_message=f"New key provisioned for '{body.tenant_id}' via '{body.source}'.",
                request=request,
            )

            log.info(
                f"[provision] tenant='{body.tenant_id}' key_id={key_record.id} "
                f"source='{body.source}' action=provisioned"
            )

            return {
                "success": True,
                "alpheric_account_id": f"acct_{body.tenant_id}",
                "alpheric_key_id": str(key_record.id),
                "api_key": raw_key,  # ← raw key returned ONCE only
                "base_url": key_record.base_url,
                "default_model": key_record.default_model,
                "status": "active",
                "created_at": key_record.created_at.isoformat(),
                "already_exists": False,
            }


@router.post("/rotate-key")
async def rotate_key(
    body: RotateKeyRequest,
    request: Request,
):
    """Revoke the current key and issue a fresh one. Raw new key returned once."""
    auth = request.headers.get("authorization")
    _verify_platform_key(auth)
    _rate_limit_ip(request.client.host if request.client else "unknown")

    async with async_session() as db:
        async with db.begin():
            key_uuid = _safe_uuid(body.alpheric_key_id)
            if key_uuid is None:
                raise HTTPException(status_code=400, detail="Invalid alpheric_key_id format.")

            old_key = (
                await db.execute(
                    select(AtlasApiKey).where(
                        AtlasApiKey.id == key_uuid,
                        AtlasApiKey.tenant_id == body.tenant_id,
                    )
                )
            ).scalar_one_or_none()

            if old_key is None:
                await _audit(
                    db,
                    tenant_id=body.tenant_id,
                    alpheric_key_id=body.alpheric_key_id,
                    action="rotated",
                    status="failure",
                    safe_message="Key not found or tenant mismatch.",
                    request=request,
                )
                raise HTTPException(
                    status_code=404,
                    detail="Key not found or does not belong to this tenant.",
                )

            # Revoke old key
            old_key.status = "revoked"
            old_key.revoked_at = now_ist()
            old_key_id = str(old_key.id)

            # Issue new key
            raw_key, key_hash, key_prefix = _generate_key()
            new_key = AtlasApiKey(
                tenant_id=old_key.tenant_id,
                tenant_name=old_key.tenant_name,
                tenant_owner_email=old_key.tenant_owner_email,
                source=old_key.source,
                key_prefix=key_prefix,
                key_hash=key_hash,
                status="active",
                default_model=old_key.default_model,
                base_url=old_key.base_url,
                rotated_at=now_ist(),
                metadata_json={"rotation_reason": body.reason, "rotated_from": old_key_id},
            )
            db.add(new_key)
            await db.flush()

            await _audit(
                db,
                tenant_id=body.tenant_id,
                alpheric_key_id=str(new_key.id),
                action="rotated",
                status="success",
                safe_message=f"Key rotated. Old key_id={old_key_id}. Reason: {body.reason}.",
                request=request,
            )

            log.info(
                f"[provision] tenant='{body.tenant_id}' old_key={old_key_id} "
                f"new_key={new_key.id} action=rotated reason='{body.reason}'"
            )

            return {
                "success": True,
                "old_key_id": old_key_id,
                "new_key_id": str(new_key.id),
                "api_key": raw_key,  # ← raw new key returned ONCE only
                "status": "active",
                "rotated_at": new_key.rotated_at.isoformat(),
            }


@router.post("/disable-key")
async def disable_key(
    body: DisableKeyRequest,
    request: Request,
):
    """Mark a key as disabled. Blocks all future chat/completion calls."""
    auth = request.headers.get("authorization")
    _verify_platform_key(auth)
    _rate_limit_ip(request.client.host if request.client else "unknown")

    async with async_session() as db:
        async with db.begin():
            key_uuid = _safe_uuid(body.alpheric_key_id)
            if key_uuid is None:
                raise HTTPException(status_code=400, detail="Invalid alpheric_key_id format.")

            key = (
                await db.execute(
                    select(AtlasApiKey).where(
                        AtlasApiKey.id == key_uuid,
                        AtlasApiKey.tenant_id == body.tenant_id,
                    )
                )
            ).scalar_one_or_none()

            if key is None:
                await _audit(
                    db,
                    tenant_id=body.tenant_id,
                    alpheric_key_id=body.alpheric_key_id,
                    action="disabled",
                    status="failure",
                    safe_message="Key not found or tenant mismatch.",
                    request=request,
                )
                raise HTTPException(
                    status_code=404,
                    detail="Key not found or does not belong to this tenant.",
                )

            disabled_at = now_ist()
            key.status = "disabled"
            key.disabled_at = disabled_at

            await _audit(
                db,
                tenant_id=body.tenant_id,
                alpheric_key_id=str(key.id),
                action="disabled",
                status="success",
                safe_message=f"Key disabled. Reason: {body.reason}.",
                request=request,
            )

            log.info(
                f"[provision] tenant='{body.tenant_id}' key_id={key.id} "
                f"action=disabled reason='{body.reason}'"
            )

            return {
                "success": True,
                "alpheric_key_id": str(key.id),
                "status": "disabled",
                "disabled_at": disabled_at.isoformat(),
            }


@router.post("/key-status")
async def key_status(
    body: KeyStatusRequest,
    request: Request,
):
    """Return key metadata. Never returns the raw API key."""
    auth = request.headers.get("authorization")
    _verify_platform_key(auth)
    _rate_limit_ip(request.client.host if request.client else "unknown")

    async with async_session() as db:
        async with db.begin():
            key_uuid = _safe_uuid(body.alpheric_key_id)
            if key_uuid is None:
                raise HTTPException(status_code=400, detail="Invalid alpheric_key_id format.")

            key = (
                await db.execute(
                    select(AtlasApiKey).where(
                        AtlasApiKey.id == key_uuid,
                        AtlasApiKey.tenant_id == body.tenant_id,
                    )
                )
            ).scalar_one_or_none()

            if key is None:
                await _audit(
                    db,
                    tenant_id=body.tenant_id,
                    alpheric_key_id=body.alpheric_key_id,
                    action="status_checked",
                    status="failure",
                    safe_message="Key not found or tenant mismatch.",
                    request=request,
                )
                raise HTTPException(
                    status_code=404,
                    detail="Key not found or does not belong to this tenant.",
                )

            # Pull today's usage from usage_records
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            key_hash = key.key_hash

            usage_rows = (
                await db.execute(
                    select(
                        func.count(UsageRecord.id).label("cnt"),
                        func.coalesce(
                            func.sum(UsageRecord.prompt_tokens + UsageRecord.completion_tokens), 0
                        ).label("toks"),
                        UsageRecord.created_at,
                    )
                    .where(UsageRecord.api_key_hash == key_hash)
                    .group_by(UsageRecord.created_at)
                )
            ).all()

            sum(1 for r in usage_rows if r.created_at and r.created_at >= today_start)
            tok_today = sum(
                r.toks for r in usage_rows if r.created_at and r.created_at >= today_start
            )
            sum(1 for r in usage_rows if r.created_at and r.created_at >= month_start)
            sum(r.toks for r in usage_rows if r.created_at and r.created_at >= month_start)

            await _audit(
                db,
                tenant_id=body.tenant_id,
                alpheric_key_id=str(key.id),
                action="status_checked",
                status="success",
                safe_message="Key status retrieved.",
                request=request,
            )

            return {
                "success": True,
                "tenant_id": key.tenant_id,
                "alpheric_key_id": str(key.id),
                "status": key.status,
                "base_url": key.base_url,
                "default_model": key.default_model,
                "created_at": key.created_at.isoformat(),
                "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                "usage": {
                    "requests_today": key.requests_total,  # simplified — full breakdown below
                    "tokens_today": tok_today,
                    "requests_month": key.requests_total,
                    "tokens_month": key.tokens_total,
                },
            }


# ── Utility ───────────────────────────────────────────────────────────────────


def _safe_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
