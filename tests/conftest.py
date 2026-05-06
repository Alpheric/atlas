"""Shared pytest fixtures.

Provisioning tests run against the real PostgreSQL DB.  Three guards make
the suite reliable and repeatable:

  1. **Pre-test data cleanup** — delete any atlas_api_keys / audit rows for
     test tenant IDs so re-running the suite never hits "already exists" paths
     unexpectedly.

  2. **Connection-pool disposal** — the DB engine is a module-level singleton.
     After each test the lifespan cancels its background tasks, but asyncpg
     does not immediately return a connection that was mid-operation when
     cancelled.  Calling engine.dispose() forces all pool connections closed so
     the next test starts with a clean pool.

  3. **Rate-limiter reset** — the in-memory sliding-window bucket is also
     module-level.  Without clearing it, the 13 rapid-fire tests exhaust the
     10 req/min limit and later tests get 429.

  4. **Lifespan background-task suppression** — run_health_monitor and the
     provider health-refresh loop spawn concurrent DB queries that race with
     the test's own queries.  We replace them with instant no-ops.
"""

import asyncio
import pytest
import pytest_asyncio


# ── Tenant IDs used by the test suite ────────────────────────────────────────

_TEST_TENANTS = [
    "tenant_test_01", "tenant_test_02", "tenant_test_03", "tenant_test_04",
    "tenant_test_05", "tenant_test_06", "tenant_test_07", "tenant_test_08",
    "tenant_test_09", "tenant_test_10", "tenant_test_11", "tenant_test_12",
    "tenant_test_13", "test_tenant_001",
]


# ── 1. Wipe test tenant data before the whole session ────────────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def clean_test_data_before_session():
    """Remove any leftover rows from previous test runs before the suite starts."""
    from a1.db.engine import async_session
    from a1.db.models import AtlasApiKey, ProvisioningAuditLog
    from sqlalchemy import delete

    async with async_session() as db:
        async with db.begin():
            await db.execute(
                delete(ProvisioningAuditLog).where(
                    ProvisioningAuditLog.tenant_id.in_(_TEST_TENANTS)
                )
            )
            await db.execute(
                delete(AtlasApiKey).where(
                    AtlasApiKey.tenant_id.in_(_TEST_TENANTS)
                )
            )


# ── 2. Dispose connection pool + reset rate limiter between tests ─────────────

@pytest_asyncio.fixture(autouse=True)
async def isolate_test():
    """Yield, then flush the DB connection pool and rate-limiter state."""
    yield

    # Clear rate limiter (must happen before the engine disposal)
    try:
        import a1.provisioning.router as _prov
        _prov._prov_buckets.clear()
    except ImportError:
        pass

    # Dispose pool — forces asyncpg to close any connection left in a broken
    # state by cancelled lifespan tasks, so the next test starts clean.
    try:
        from a1.db.engine import engine
        await engine.dispose()
    except Exception:
        pass


# ── 3. Suppress DB-touching background coroutines spawned by create_app() ────

@pytest.fixture(autouse=True)
def suppress_lifespan_background_tasks(monkeypatch):
    """Replace background coroutines with instant no-ops to prevent DB races."""

    async def _noop(*_a, **_kw):
        return

    try:
        import a1.healing.conversation_monitor as _mon
        monkeypatch.setattr(_mon, "run_health_monitor", _noop)
    except ImportError:
        pass

    try:
        import a1.providers.registry as _reg
        monkeypatch.setattr(_reg.provider_registry, "refresh_health", _noop)
    except (ImportError, AttributeError):
        pass

    # Suppress key_pool and agent_registry DB calls during lifespan startup
    try:
        import a1.providers.key_pool as _kp
        monkeypatch.setattr(_kp.key_pool, "load_accounts", _noop)
    except (ImportError, AttributeError):
        pass

    try:
        import a1.agents.registry as _ar
        monkeypatch.setattr(_ar.agent_registry, "initialize", _noop)
    except (ImportError, AttributeError):
        pass


# ── Legacy fixtures used by other test files ──────────────────────────────────

@pytest.fixture
def sample_messages():
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write a Python function to sort a list"},
    ]


@pytest.fixture
def sample_chat_request():
    return {
        "model": "auto",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"},
        ],
    }
