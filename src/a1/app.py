import asyncio
import os
import sys
from contextlib import asynccontextmanager

# Force UTF-8 encoding on Windows to handle emojis from Claude CLI
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Also fix stdout/stderr encoding
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from a1.common.logging import setup_logging
from config.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.debug)

    # Validate critical configuration
    if not settings.database_url:
        print(
            "FATAL: A1_DATABASE_URL is not set. "
            "Set it in your .env file or environment. Example:\n"
            "  A1_DATABASE_URL=sqlite+aiosqlite:///./a1_trainer.db  (dev)\n"
            "  A1_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/a1  (prod)"
        )
        sys.exit(1)

    # Auto-create tables for SQLite (dev mode)
    if "sqlite" in settings.database_url:
        import a1.db.models  # noqa — ensure all models are registered
        from a1.db.engine import create_tables

        await create_tables()
        from a1.common.logging import get_logger

        get_logger("startup").info("SQLite tables created")

    # Initialize OpenTelemetry (no-op if otlp_endpoint is empty)
    from a1.common.telemetry import setup_telemetry

    setup_telemetry(app, settings)

    # Initialize GPTCache (no-op if cache_enabled is False)
    from a1.proxy.cache import init_cache

    init_cache(settings)

    # Load multi-account key pool (skip if DB not ready)
    try:
        from a1.providers.key_pool import key_pool

        await key_pool.load_accounts()
        # HIGH-02: Refuse to start if provider accounts exist without an encryption key.
        # Without it, encrypt_key() silently stores API keys in plaintext.
        if key_pool.get_providers_with_accounts() and not settings.encryption_key:
            from a1.common.logging import get_logger as _get_log

            _get_log("startup").critical(
                "A1_ENCRYPTION_KEY is not set but provider accounts exist in the database. "
                "Provider API keys would be stored in plaintext — critical security risk. "
                'Generate: python -c "from cryptography.fernet import Fernet; '  # noqa: E501
                'print(Fernet.generate_key().decode())"'
                " and set A1_ENCRYPTION_KEY in .env. Server is refusing to start."
            )
            sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass  # key pool load fails gracefully without DB

    # Register LLM providers
    from a1.providers.registry import provider_registry

    await provider_registry.initialize()

    # Initialize Agent Registry (loads agents from DB into memory)
    from a1.agents.registry import agent_registry

    await agent_registry.initialize()

    # Register computer use tools (if enabled)
    from a1.tools.computer import register_computer_tools

    register_computer_tools()

    # Register code interpreter into the proxy tool_registry
    from a1.proxy.pipeline import tool_registry as _proxy_tool_registry
    from a1.tools.code_interpreter import register as _register_ci

    _register_ci(_proxy_tool_registry)

    # Periodic health refresh every 5 minutes (background)
    async def _health_refresh_loop():
        while True:
            await asyncio.sleep(300)
            try:
                await provider_registry.refresh_health()
            except Exception as e:
                from a1.common.logging import get_logger

                get_logger("startup").warning(f"Periodic health refresh failed: {e}")

    _health_task = asyncio.create_task(_health_refresh_loop())

    # Initialize ARQ pool + verify Redis reachable
    from a1.common.logging import get_logger

    _startup_log = get_logger("startup")
    try:
        from a1.dependencies import init_arq_pool

        _arq = await init_arq_pool()
        await _arq.ping()
        _startup_log.info("ARQ pool initialized — Redis reachable")
    except Exception as _e:
        _startup_log.warning(f"ARQ pool init failed (training dispatch unavailable): {_e}")

    # Initialize Redis-backed session persistence (graceful fallback to in-memory)
    if settings.redis_url:
        from a1.session.manager import session_manager

        await session_manager.init_redis(settings.redis_url)
        _startup_log.info(f"Session backend: {'Redis' if session_manager._redis else 'in-memory'}")

    # Warm up local Ollama models (background, non-blocking)
    if settings.warm_up_models:
        from a1.common.logging import get_logger

        log = get_logger("startup")
        log.info(f"Warming up {len(settings.warm_up_models)} models...")

        async def _warm_up():
            import httpx

            for model in settings.warm_up_models:
                ollama = provider_registry.get_provider("ollama")
                if ollama:
                    server_url = ollama.get_server_for_model(model)
                    try:
                        async with httpx.AsyncClient(base_url=server_url, timeout=300.0) as client:
                            await client.post(
                                "/api/generate",
                                json={
                                    "model": model,
                                    "prompt": "hi",
                                    "options": {"num_predict": 1},
                                },
                            )
                        log.info(f"  Warmed up {model} on {server_url}")
                    except Exception as e:
                        log.warning(f"  Failed to warm up {model}: {e}")

        asyncio.create_task(_warm_up())

    # Initialize web search providers (no-op if no API keys configured)
    from a1.search.providers.registry import init_search_providers

    init_search_providers()

    # Start conversation health monitor (background task, fire-and-forget)
    from a1.healing.conversation_monitor import run_health_monitor

    _monitor_task = asyncio.create_task(run_health_monitor())
    _startup_log.info(
        f"Conversation health monitor started "
        f"(interval={settings.health_monitor_interval_seconds}s)"
    )

    yield

    # Cleanup
    _health_task.cancel()
    _monitor_task.cancel()
    from a1.dependencies import _arq_pool, _redis

    if _redis:
        await _redis.aclose()
    if _arq_pool:
        await _arq_pool.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Alpheric.AI — Atlas Model Family | AI Middleware & Training Platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    from a1.dashboard.router import _public_router as dashboard_public_router
    from a1.dashboard.router import _ws_router as dashboard_ws_router
    from a1.dashboard.router import router as dashboard_router
    from a1.proxy.router import router as proxy_router

    app.include_router(proxy_router)
    app.include_router(dashboard_public_router)  # auth/login — no auth required
    app.include_router(dashboard_router)
    app.include_router(dashboard_ws_router)

    # P3: WebSocket chat rooms
    from a1.chat.ws import router as chat_ws_router

    app.include_router(chat_ws_router)

    # P4: Notebook API
    from a1.notebook.router import router as notebook_router

    app.include_router(notebook_router)

    # Veo video generation endpoints
    from a1.proxy.veo_router import router as veo_router

    app.include_router(veo_router)

    # Prometheus /metrics endpoint
    from a1.common.prometheus import router as prometheus_router

    app.include_router(prometheus_router)

    # Provisioning API (OneDesk / platform-to-platform)
    from a1.provisioning.router import router as provisioning_router

    app.include_router(provisioning_router)

    # MCP server — mount SSE transport at /mcp
    from a1.mcp.server import mcp as _mcp

    app.mount("/mcp", _mcp.sse_app())

    # Atlas CLI installer — serve install scripts + pre-built bundle
    # install.sh  → curl -fsSL https://atlas.alpheric.ai/install.sh | bash
    # install.ps1 → irm https://atlas.alpheric.ai/install.ps1 | iex
    import pathlib as _pathlib

    from fastapi.responses import FileResponse as _FileResponse
    from fastapi.staticfiles import StaticFiles as _StaticFiles

    _downloads_dir = _pathlib.Path(__file__).parent.parent.parent / "public" / "downloads"
    if _downloads_dir.exists():
        app.mount("/downloads", _StaticFiles(directory=str(_downloads_dir)), name="downloads")

        @app.get("/install.sh", include_in_schema=False)
        async def install_sh():
            return _FileResponse(
                str(_downloads_dir / "install.sh"),
                media_type="text/plain",
                headers={"Content-Disposition": "inline"},
            )

        @app.get("/install.ps1", include_in_schema=False)
        async def install_ps1():
            return _FileResponse(
                str(_downloads_dir / "install.ps1"),
                media_type="text/plain",
                headers={"Content-Disposition": "inline"},
            )

    # Request ID + logging middleware
    import uuid as _uuid

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    from a1.common.logging import get_logger
    from a1.proxy.core_pipeline import request_id_var

    _req_log = get_logger("requests")

    class RequestMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            # Generate or propagate request ID
            rid = request.headers.get("X-Request-Id") or f"req_{_uuid.uuid4().hex[:16]}"
            request_id_var.set(rid)
            _req_log.info(
                f">>> {request.method} {request.url.path} [{rid}] "
                f"from {request.client.host if request.client else '?'}"
            )
            response = await call_next(request)
            response.headers["X-Request-Id"] = rid
            _req_log.info(
                f"<<< {request.method} {request.url.path} [{rid}] -> {response.status_code}"
            )
            return response

    app.add_middleware(RequestMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": settings.app_name}

    return app


app = create_app()
