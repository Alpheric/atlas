"""Provider management, accounts, Ollama, server status, and playground endpoints.

Endpoints:
  GET    /providers
  POST   /providers/refresh
  GET    /accounts
  POST   /accounts
  DELETE /accounts/{account_id}
  POST   /accounts/{account_id}/test
  GET    /ollama/models
  POST   /ollama/pull
  DELETE /ollama/models/{name}
  GET    /servers
  POST   /playground
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from a1.dependencies import get_db
from a1.providers.registry import provider_registry

router = APIRouter()


# --- Providers ---
@router.get("/providers")
async def list_providers():
    providers = provider_registry.list_providers()

    # Enrich claude-cli entry with per-account pool status if applicable
    cli_provider = provider_registry.get_provider("claude-cli")
    if cli_provider is not None:
        from a1.providers.claude_cli import ClaudeCLIPool
        if isinstance(cli_provider, ClaudeCLIPool):
            for p in providers:
                if p["name"] == "claude-cli":
                    p["pool"] = cli_provider.pool_status()
                    break

    # Enrich vertex entry with config info
    from config.settings import settings as _s
    for p in providers:
        if p["name"] == "vertex":
            p["auth_type"] = _s.vertex_auth_type
            p["project_id"] = _s.vertex_project_id or None
            p["default_model"] = _s.vertex_default_model
            p["web_search_enabled"] = _s.vertex_web_search_enabled
            break

    # Add Veo if configured (separate from vertex provider registry)
    if _s.vertex_project_id:
        from a1.providers.veo import veo_provider
        providers.append({
            "name": "veo",
            "healthy": bool(_s.vertex_project_id),  # assume ok if project set; health checked separately
            "models": [m["name"] for m in veo_provider.list_models()],
            "model_count": len(veo_provider.list_models()),
            "project_id": _s.vertex_project_id,
            "supports_vision": True,
            "supports_streaming": False,
            "supports_tools": False,
            "description": "Google Veo — text-to-video & image-to-video generation",
        })

    return {"data": providers}


@router.post("/providers/refresh")
async def refresh_providers():
    # Re-discover Ollama models (picks up new pulls on remote servers)
    ollama = provider_registry.get_provider("ollama")
    if ollama and hasattr(ollama, "discover_models"):
        await ollama.discover_models()
    await provider_registry.refresh_health()
    return {"status": "refreshed", "providers": provider_registry.list_providers()}


# --- Provider Accounts (multi-key management) ---
@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select

    from a1.db.models import ProviderAccount

    result = await db.execute(
        select(ProviderAccount).order_by(ProviderAccount.provider, ProviderAccount.priority.desc())
    )
    accounts = result.scalars().all()
    data = [
        {
            "id": str(a.id),
            "provider": a.provider,
            "name": a.name,
            "is_active": a.is_active,
            "priority": a.priority,
            "rate_limit_rpm": a.rate_limit_rpm,
            "monthly_budget_usd": float(a.monthly_budget_usd) if a.monthly_budget_usd else None,
            "current_month_cost_usd": float(a.current_month_cost_usd),
            "total_requests": a.total_requests,
            "total_tokens": a.total_tokens,
            "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
            "last_error": a.last_error,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ]

    # Append Claude CLI pool accounts (runtime, not DB-persisted)
    from a1.providers.claude_cli import ClaudeCLIPool
    cli = provider_registry.get_provider("claude-cli")
    if isinstance(cli, ClaudeCLIPool):
        for s in cli.pool_status():
            data.append({
                "id": f"cli:{s['user']}",
                "provider": "claude-cli",
                "name": s["user"],
                "is_active": s["healthy"],
                "priority": 10,
                "rate_limit_rpm": None,
                "monthly_budget_usd": None,
                "current_month_cost_usd": round(s.get("cost_usd", 0.0), 6),
                "total_requests": s.get("requests", 0),
                "total_tokens": s.get("input_tokens", 0) + s.get("output_tokens", 0),
                "last_used_at": None,
                "last_error": None if s["healthy"] else "Not logged in — run: claude login",
                "created_at": None,
                "cli_path": s.get("cli_path"),
                "active_sessions": s.get("sessions", 0),
            })

    # Append Vertex account (runtime, configured via .env — not DB-persisted)
    from config.settings import settings as _s
    vertex = provider_registry.get_provider("vertex")
    if vertex:
        vertex_healthy = provider_registry.is_healthy("vertex")
        auth_label = (
            f"Project: {_s.vertex_project_id}" if _s.vertex_auth_type == "service_account"
            else f"API Key: {'*' * 8 + _s.vertex_api_key[-4:] if _s.vertex_api_key else 'not set'}"
        )
        data.append({
            "id": "vertex:env",
            "provider": "vertex",
            "name": f"Gemini ({_s.vertex_default_model or 'gemini-2.5-pro'})",
            "is_active": vertex_healthy,
            "priority": 20,
            "rate_limit_rpm": None,
            "monthly_budget_usd": None,
            "current_month_cost_usd": 0.0,
            "total_requests": 0,
            "total_tokens": 0,
            "last_used_at": None,
            "last_error": None if vertex_healthy else "Vertex unhealthy — check API key / project",
            "created_at": None,
            "auth_type": _s.vertex_auth_type,
            "auth_label": auth_label,
            "project_id": _s.vertex_project_id,
            "default_model": _s.vertex_default_model,
        })

    return {"data": data}


@router.post("/accounts")
async def create_account(
    provider: str,
    name: str,
    api_key: str,
    priority: int = 0,
    rate_limit_rpm: int | None = None,
    monthly_budget_usd: float | None = None,
    db: AsyncSession = Depends(get_db),
):
    from a1.db.models import ProviderAccount
    from a1.providers.key_pool import encrypt_key, key_pool

    account = ProviderAccount(
        provider=provider,
        name=name,
        api_key_encrypted=encrypt_key(api_key),
        priority=priority,
        rate_limit_rpm=rate_limit_rpm,
        monthly_budget_usd=monthly_budget_usd,
    )
    db.add(account)
    await db.flush()
    await key_pool.load_accounts()  # reload pool
    return {"id": str(account.id), "status": "created"}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import delete as sql_delete

    from a1.db.models import ProviderAccount
    from a1.providers.key_pool import key_pool

    await db.execute(sql_delete(ProviderAccount).where(ProviderAccount.id == uuid.UUID(account_id)))
    await key_pool.load_accounts()
    return {"status": "deleted"}


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: str, db: AsyncSession = Depends(get_db)):
    # CLI pool accounts have IDs like "cli:neeraj" — handle separately
    if account_id.startswith("cli:"):
        unix_user = account_id[4:]
        from a1.providers.claude_cli import ClaudeCLIAccount, ClaudeCLIPool
        from a1.proxy.request_models import ChatCompletionRequest, MessageInput

        pool = provider_registry.get_provider("claude-cli")
        # Find the specific account in the pool
        account_obj = None
        if isinstance(pool, ClaudeCLIPool):
            for acc in pool.accounts:
                if acc.unix_user == unix_user:
                    account_obj = acc
                    break
        if not account_obj:
            account_obj = ClaudeCLIAccount(unix_user)

        try:
            req = ChatCompletionRequest(
                model="claude-haiku-4-5-20251001",
                messages=[MessageInput(role="user", content="Reply with one word: OK")],
                max_tokens=5,
            )
            resp = await account_obj.complete(req)
            content = resp.choices[0].message.content if resp.choices else ""
            return {"status": "ok", "message": f"Account active — response: {content.strip()}", "user": unix_user}
        except Exception as e:
            return {"status": "error", "message": str(e), "user": unix_user}

    # DB-managed API key accounts
    from a1.db.models import ProviderAccount
    from a1.providers.key_pool import decrypt_key

    try:
        account_uuid = uuid.UUID(account_id)
    except ValueError:
        raise HTTPException(400, f"Invalid account ID: {account_id}")

    from sqlalchemy import select as sa_select
    result = await db.execute(
        sa_select(ProviderAccount).where(ProviderAccount.id == account_uuid)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    try:
        import litellm

        api_key = decrypt_key(account.api_key_encrypted)
        await litellm.acompletion(
            model="gpt-4o-mini" if account.provider == "openai" else "claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            api_key=api_key,
        )
        return {"status": "ok", "message": "Key is valid"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- Ollama Management ---
@router.get("/ollama/models")
async def ollama_models():
    from a1.providers.registry import provider_registry

    ollama = provider_registry.get_provider("ollama")
    if not ollama:
        return {"data": [], "servers": []}
    return {
        "data": [
            {"name": m.name, "provider": m.provider, "context_window": m.context_window}
            for m in ollama.list_models()
        ],
        "servers": ollama.list_servers() if hasattr(ollama, "list_servers") else [],
    }


@router.post("/ollama/pull")
async def ollama_pull(name: str, server_url: str | None = None):
    import httpx

    from config.settings import settings

    url = server_url or (
        settings.ollama_servers[0] if settings.ollama_servers else settings.ollama_base_url
    )
    async with httpx.AsyncClient(base_url=url, timeout=600.0) as client:
        resp = await client.post("/api/pull", json={"name": name})
        return resp.json()


@router.delete("/ollama/models/{name}")
async def ollama_delete(name: str, server_url: str | None = None):
    import httpx

    from config.settings import settings

    url = server_url or (
        settings.ollama_servers[0] if settings.ollama_servers else settings.ollama_base_url
    )
    async with httpx.AsyncClient(base_url=url, timeout=30.0) as client:
        resp = await client.delete("/api/delete", json={"name": name})
        return resp.json()


# --- Server Status ---
@router.get("/servers")
async def server_status():
    """Get status of all infrastructure servers."""
    ollama = provider_registry.get_provider("ollama")
    servers = []
    if ollama and hasattr(ollama, "list_servers"):
        for s in ollama.list_servers():
            servers.append({**s, "type": "ollama"})
    return {"data": servers}


# --- Prompt Playground ---
@router.post("/playground")
async def playground(body: dict):
    """Test a prompt against any available model."""
    import time as _time

    from a1.proxy.request_models import ChatCompletionRequest, MessageInput

    model = body.get("model", "alpheric-1")
    prompt = body.get("prompt", "")
    system_prompt = body.get("system_prompt", "")
    temperature = body.get("temperature", 0.7)
    max_tokens = body.get("max_tokens", 500)

    messages = []
    if system_prompt:
        messages.append(MessageInput(role="system", content=system_prompt))
    messages.append(MessageInput(role="user", content=prompt))

    # Resolve Atlas model aliases (Atlas, atlas-*, alpheric-1, auto, local) to actual provider model
    _ATLAS_ALIASES = {"Atlas", "alpheric-1", "auto", "auto:fast", "auto:cheap", "local"}
    _ATLAS_DEFAULT_MODEL = "claude-sonnet-4-20250514"
    actual_model = model
    if model.startswith("atlas-") or model.lower().startswith("atlas") or model in _ATLAS_ALIASES:
        actual_model = _ATLAS_DEFAULT_MODEL

    provider = provider_registry.get_provider_for_model(actual_model)
    if not provider:
        from fastapi import HTTPException

        raise HTTPException(404, f"No provider for model: {model}")

    req = ChatCompletionRequest(
        model=actual_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    start = _time.time()
    try:
        resp = await provider.complete(req)
        latency = int((_time.time() - start) * 1000)
        content = resp.choices[0].message.content if resp.choices else ""
        return {
            "model": model,
            "provider": provider.name,
            "content": content,
            "latency_ms": latency,
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
            "cost_usd": provider.estimate_cost(
                resp.usage.prompt_tokens, resp.usage.completion_tokens, model
            ),
        }
    except Exception as e:
        latency = int((_time.time() - start) * 1000)
        return {"model": model, "error": str(e), "latency_ms": latency}


# --- OpenClaw Gateway ---

@router.get("/openclaw/status")
async def openclaw_status():
    """Return OpenClaw gateway connectivity status and discovered models."""
    from config.settings import settings
    import httpx

    url = settings.openclaw_url
    if not url:
        return {"enabled": False, "url": None, "healthy": False, "models": []}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{url}/v1/models")
            if r.status_code == 200:
                data = r.json()
                models = [m.get("id") for m in data.get("data", [])]
                return {"enabled": True, "url": url, "healthy": True, "models": models}
    except Exception as e:
        return {"enabled": True, "url": url, "healthy": False, "models": [], "error": str(e)}

    return {"enabled": True, "url": url, "healthy": False, "models": []}


@router.post("/openclaw/discover")
async def openclaw_discover():
    """Trigger model discovery on the OpenClaw gateway."""
    from config.settings import settings
    import httpx

    url = settings.openclaw_url
    if not url:
        return {"status": "disabled", "models": []}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/v1/models")
            models = [m.get("id") for m in r.json().get("data", [])] if r.status_code == 200 else []
            return {"status": "ok", "models": models, "count": len(models)}
    except Exception as e:
        return {"status": "error", "error": str(e), "models": []}


@router.post("/openclaw/import-history")
async def openclaw_import_history(limit: int = 1000):
    """Import conversation history from the OpenClaw gateway."""
    from config.settings import settings
    import httpx

    url = settings.openclaw_url
    if not url:
        return {"status": "disabled", "imported": 0}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{url}/conversations", params={"limit": limit})
            if r.status_code != 200:
                return {"status": "error", "error": f"Gateway returned {r.status_code}", "imported": 0}
            conversations = r.json().get("data", [])
            return {"status": "ok", "imported": len(conversations)}
    except Exception as e:
        return {"status": "error", "error": str(e), "imported": 0}
