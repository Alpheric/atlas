"""Prompt registry — DB-backed, versioned prompt templates with code fallback.

Phase 2.1. Lets prompts live in the `prompt_versions` table so they can be
edited and A/B-tested without a redeploy. Always falls back to a code-supplied
default when no active DB version exists, so behavior is preserved even with an
empty table.

Usage:
    from a1.common.prompt_registry import get_prompt

    template = await get_prompt("self_critique", default=_CRITIQUE_PROMPT)
    text = template.format(...)

A short in-memory TTL cache avoids a DB hit on every request.
"""

import time

from a1.common.logging import get_logger

log = get_logger("prompt_registry")

# name -> (content, fetched_at)
_cache: dict[str, tuple[str, float]] = {}
_TTL_SECONDS = 60.0


async def get_prompt(name: str, default: str, model: str | None = None) -> str:
    """Return the active prompt content for `name`, else `default`.

    If `model` is given, a model-scoped active version takes precedence over a
    global (model IS NULL) one. Cached for _TTL_SECONDS. Any DB error falls
    back to `default` — this must never break a request.
    """
    cache_key = f"{name}::{model or '*'}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and (now - cached[1]) < _TTL_SECONDS:
        return cached[0]

    content = default
    try:
        from sqlalchemy import select

        from a1.db.engine import async_session
        from a1.db.models import PromptVersion

        async with async_session() as session:
            stmt = (
                select(PromptVersion.content, PromptVersion.model)
                .where(PromptVersion.name == name, PromptVersion.is_active.is_(True))
                .order_by(PromptVersion.version.desc())
            )
            rows = (await session.execute(stmt)).all()

        if rows:
            # Prefer a model-scoped match, else the first global one.
            chosen = None
            if model:
                chosen = next((c for c, m in rows if m == model), None)
            if chosen is None:
                chosen = next((c for c, m in rows if m is None), None)
            if chosen is None:
                chosen = rows[0][0]
            content = chosen
    except Exception as e:
        log.debug(f"prompt_registry fallback for '{name}': {e}")
        content = default

    _cache[cache_key] = (content, now)
    return content


def invalidate(name: str | None = None) -> None:
    """Clear the cache (call after creating/activating a version)."""
    if name is None:
        _cache.clear()
    else:
        for k in list(_cache):
            if k.startswith(f"{name}::"):
                _cache.pop(k, None)
