"""MCP ASGI auth middleware — validates Bearer / x-api-key before forwarding."""

from __future__ import annotations

from typing import Callable

from a1.common.logging import get_logger

log = get_logger("mcp.auth")


class MCPAuthMiddleware:
    """Thin ASGI middleware that enforces API key auth on the MCP SSE app.

    FastMCP mounts as a raw ASGI app so FastAPI's `Depends(verify_api_key)`
    doesn't reach it. This wrapper sits in front and rejects unauthenticated
    requests with 401 before they touch the MCP layer.

    Accepted formats (same as the rest of the API):
      Authorization: Bearer <key>
      x-api-key: <key>
    """

    def __init__(self, app: Callable) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        # Extract key from headers
        headers = dict(scope.get("headers", []))
        auth_header: bytes = headers.get(b"authorization", b"")
        xapi_header: bytes = headers.get(b"x-api-key", b"")

        key = ""
        if auth_header:
            parts = auth_header.decode("utf-8", errors="replace").split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                key = parts[1].strip()
        if not key and xapi_header:
            key = xapi_header.decode("utf-8", errors="replace").strip()

        # Validate
        if not await _is_valid_key(key):
            log.warning(f"[mcp.auth] rejected unauthenticated {scope['type']} request")
            if scope["type"] == "http":
                await _send_401(send)
            return

        await self._app(scope, receive, send)


async def _is_valid_key(key: str) -> bool:
    """Accept master env key or any DB-stored user key."""
    if not key:
        return False
    try:
        from a1.common.auth import _verify_key_in_db, hash_key
        from config.settings import settings

        # Master env keys
        if key in (settings.api_keys or []):
            return True
        # OneDesk platform key
        if settings.alpheric_ai_platform_api_key and key == settings.alpheric_ai_platform_api_key:
            return True
        # DB keys (dashboard + tenant)
        return await _verify_key_in_db(hash_key(key))
    except Exception:
        return False


async def _send_401(send) -> None:
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            [b"content-type", b"application/json"],
            [b"www-authenticate", b'Bearer realm="Atlas MCP"'],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": b'{"detail":"Not authenticated. Provide Authorization: Bearer <api-key>"}',
    })
