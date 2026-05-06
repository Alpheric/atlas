"""Provisioning API test suite.

Tests all 13 required scenarios:
 1.  Missing platform key → 401
 2.  Invalid platform key → 401
 3.  Valid key provisions tenant key
 4.  Provisioning same tenant twice is idempotent
 5.  Rotate key returns new key, old key revoked
 6.  Disabled key cannot call /v1/chat/completions
 7.  Key-status never returns raw API key
 8.  Chat completion works with tenant key (mocked)
 9.  Platform key cannot be used for chat completion
10.  Raw key never stored in DB
11.  Raw key never logged
12.  Audit logs are created
13.  Response base_url is https://atlas.alpheric.ai/v1
"""

import hashlib
import logging
import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.skipif(
    "postgresql" not in os.getenv("A1_DATABASE_URL", ""),
    reason="Provisioning tests require PostgreSQL",
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

PLATFORM_KEY = "plt-test-platform-key-for-testing-only"
FAKE_ATLAS_KEY = "sk-atlas-FwcHfmI5qWzbohi2prMoixYBHAxEoxKEtN4qK2K9i38"


@pytest.fixture(autouse=True)
def set_platform_key(monkeypatch):
    """Inject a test platform key into settings."""
    from config.settings import settings

    monkeypatch.setattr(settings, "alpheric_ai_platform_api_key", PLATFORM_KEY)
    monkeypatch.setattr(settings, "alpheric_ai_base_url", "https://atlas.alpheric.ai/v1")
    monkeypatch.setattr(settings, "alpheric_ai_default_model", "Atlas")
    monkeypatch.setattr(settings, "atlas_api_key_prefix", "sk-atlas")


@pytest_asyncio.fixture
async def client():
    from a1.app import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def prov_headers(key: str = PLATFORM_KEY) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ── Helper: provision a tenant and return response ────────────────────────────


async def _provision(client, tenant_id: str = "test_tenant_001", **kwargs) -> dict:
    body = {
        "tenant_id": tenant_id,
        "tenant_name": "Test Tenant",
        "tenant_owner_email": "test@example.com",
        "source": "onedesk",
        **kwargs,
    }
    r = await client.post("/api/provision/tenant", json=body, headers=prov_headers())
    assert r.status_code == 200, r.text
    return r.json()


# ── Test 1: Missing platform key → 401 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_01_missing_platform_key(client):
    r = await client.post(
        "/api/provision/tenant",
        json={"tenant_id": "t1", "tenant_name": "T", "tenant_owner_email": "t@t.com"},
    )
    assert r.status_code == 401
    assert "Missing" in r.json()["detail"] or r.status_code == 401


# ── Test 2: Invalid platform key → 401 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_02_invalid_platform_key(client):
    r = await client.post(
        "/api/provision/tenant",
        json={"tenant_id": "t1", "tenant_name": "T", "tenant_owner_email": "t@t.com"},
        headers=prov_headers("wrong-key-totally-invalid"),
    )
    assert r.status_code == 401


# ── Test 3: Valid key provisions tenant ───────────────────────────────────────


@pytest.mark.asyncio
async def test_03_provision_tenant(client):
    data = await _provision(client, "tenant_test_03")
    assert data["success"] is True
    assert data["api_key"].startswith("sk-atlas-")
    assert data["alpheric_key_id"] != ""
    assert data["status"] == "active"
    assert data["already_exists"] is False


# ── Test 4: Idempotent — provision same tenant twice ──────────────────────────


@pytest.mark.asyncio
async def test_04_idempotent_provision(client):
    first = await _provision(client, "tenant_test_04")
    assert first["api_key"] is not None  # raw key returned first time

    second = await _provision(client, "tenant_test_04")
    assert second["already_exists"] is True
    assert second["api_key"] is None  # raw key NOT returned on duplicate
    assert second["alpheric_key_id"] == first["alpheric_key_id"]  # same key id


# ── Test 5: Rotate key ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_05_rotate_key(client):
    first = await _provision(client, "tenant_test_05")
    old_key_id = first["alpheric_key_id"]
    old_raw = first["api_key"]

    r = await client.post(
        "/api/provision/rotate-key",
        json={
            "tenant_id": "tenant_test_05",
            "alpheric_key_id": old_key_id,
            "reason": "test_rotation",
        },
        headers=prov_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["old_key_id"] == old_key_id
    assert data["new_key_id"] != old_key_id
    assert data["api_key"].startswith("sk-atlas-")
    assert data["api_key"] != old_raw  # brand new key
    assert data["status"] == "active"

    # Verify old key is now revoked — status check should show new key
    status_r = await client.post(
        "/api/provision/key-status",
        json={"tenant_id": "tenant_test_05", "alpheric_key_id": old_key_id},
        headers=prov_headers(),
    )
    # old key still findable but revoked
    status_data = status_r.json()
    assert status_data.get("status") == "revoked" or status_r.status_code == 404


# ── Test 6: Disabled key cannot use /v1/chat/completions ─────────────────────


@pytest.mark.asyncio
async def test_06_disabled_key_blocked(client):
    pdata = await _provision(client, "tenant_test_06")
    key_id = pdata["alpheric_key_id"]
    tenant_key = pdata["api_key"]

    # Disable it
    dis_r = await client.post(
        "/api/provision/disable-key",
        json={
            "tenant_id": "tenant_test_06",
            "alpheric_key_id": key_id,
            "reason": "test_disable",
        },
        headers=prov_headers(),
    )
    assert dis_r.status_code == 200
    assert dis_r.json()["status"] == "disabled"

    # Attempt chat completion — must be rejected
    from config.settings import settings

    list(settings.api_keys)
    # Ensure tenant key is NOT in master list
    assert tenant_key not in settings.api_keys

    chat_r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "atlas-code",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"Authorization": f"Bearer {tenant_key}"},
    )
    # Should be 401 (disabled key) not 200
    assert chat_r.status_code in (401, 403)


# ── Test 7: key-status never returns raw API key ──────────────────────────────


@pytest.mark.asyncio
async def test_07_status_no_raw_key(client):
    pdata = await _provision(client, "tenant_test_07")
    key_id = pdata["alpheric_key_id"]

    r = await client.post(
        "/api/provision/key-status",
        json={"tenant_id": "tenant_test_07", "alpheric_key_id": key_id},
        headers=prov_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    assert "api_key" not in data  # raw key NEVER in status response
    # Also ensure the raw key value isn't hiding in any field
    raw_key = pdata["api_key"]
    assert raw_key not in str(data)


# ── Test 8: Chat completion works with valid tenant key (mock routing) ─────────


@pytest.mark.asyncio
async def test_08_chat_works_with_tenant_key(client):
    from config.settings import settings

    pdata = await _provision(client, "tenant_test_08")
    tenant_key = pdata["api_key"]

    # Ensure master key list is non-empty so auth is enforced
    assert settings.api_keys, "api_keys must be set for this test"
    assert tenant_key not in settings.api_keys

    # Mock the pipeline to avoid actual LLM calls
    from a1.proxy.core_pipeline import CorePipeline, CorePipelineResult

    mock_result = CorePipelineResult(
        assistant_text="Hello from Atlas",
        provider_name="vertex",
        model_name="gemini-2.5-pro",
        task_type="chat",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    with patch.object(CorePipeline, "execute", new=AsyncMock(return_value=mock_result)):
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "atlas-code",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            headers={"Authorization": f"Bearer {tenant_key}"},
        )
    # Should succeed (200) not be rejected
    assert r.status_code == 200


# ── Test 9: Platform key cannot be used for chat completion ───────────────────


@pytest.mark.asyncio
async def test_09_platform_key_rejected_for_chat(client):
    from config.settings import settings

    # Platform key should NOT be in api_keys list
    assert PLATFORM_KEY not in settings.api_keys
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "atlas-code", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {PLATFORM_KEY}"},
    )
    assert r.status_code in (401, 403)


# ── Test 10: Raw key never stored in DB ───────────────────────────────────────


@pytest.mark.asyncio
async def test_10_raw_key_not_in_db(client):
    from sqlalchemy import select

    from a1.db.engine import async_session
    from a1.db.models import AtlasApiKey

    pdata = await _provision(client, "tenant_test_10")
    raw_key = pdata["api_key"]
    key_id = pdata["alpheric_key_id"]

    async with async_session() as session:
        row = (
            await session.execute(select(AtlasApiKey).where(AtlasApiKey.id == key_id))
        ).scalar_one_or_none()

    assert row is not None
    assert row.key_hash == sha256(raw_key)  # hash stored correctly
    assert raw_key not in (row.key_prefix or "")  # prefix is only first 18 chars
    # Ensure raw key is NOT stored anywhere in the row's string representation
    row_str = str(row.__dict__)
    assert raw_key not in row_str


# ── Test 11: Raw key never logged ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_11_raw_key_not_logged(client, caplog):
    with caplog.at_level(logging.DEBUG, logger="a1.provisioning"):
        pdata = await _provision(client, "tenant_test_11")
    raw_key = pdata["api_key"]
    # The raw key must not appear anywhere in captured log output
    assert raw_key not in caplog.text


# ── Test 12: Audit logs are created ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_12_audit_logs_created(client):
    from sqlalchemy import select

    from a1.db.engine import async_session
    from a1.db.models import ProvisioningAuditLog

    await _provision(client, "tenant_test_12")

    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(ProvisioningAuditLog)
                    .where(ProvisioningAuditLog.tenant_id == "tenant_test_12")
                    .order_by(ProvisioningAuditLog.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) >= 1
    row = rows[0]
    assert row.action == "provisioned"
    assert row.status == "success"
    # Ensure audit log contains no raw key
    assert "sk-atlas-" not in (row.safe_message or "")


# ── Test 13: base_url is https://atlas.alpheric.ai/v1 ────────────────────────


@pytest.mark.asyncio
async def test_13_base_url_correct(client):
    data = await _provision(client, "tenant_test_13")
    assert data["base_url"] == "https://atlas.alpheric.ai/v1"

    key_id = data["alpheric_key_id"]
    status_r = await client.post(
        "/api/provision/key-status",
        json={"tenant_id": "tenant_test_13", "alpheric_key_id": key_id},
        headers=prov_headers(),
    )
    assert status_r.json()["base_url"] == "https://atlas.alpheric.ai/v1"
