"""Response caching layer.

Two cache mechanisms:
1. GPTCache (semantic) — similarity-based, ONNX + FAISS, controlled by settings.cache_enabled.
2. TaskResponseCache (TTL) — exact content hash, per-task-type TTL, always active when
   settings.task_cache_enabled is True. Lightweight in-memory dict, no external deps.
"""

import hashlib
import json
import os
import time
from pathlib import Path

from a1.common.logging import get_logger

log = get_logger("proxy.cache")

_cache = None
_initialized = False


def init_cache(settings) -> None:
    """Initialize GPTCache with semantic similarity matching."""
    global _cache, _initialized

    if not settings.cache_enabled or _initialized:
        return

    try:
        from gptcache import Cache
        from gptcache.adapter.api import init_similar_cache

        cache_dir = os.path.dirname(settings.cache_db_path)
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

        _cache = Cache()
        init_similar_cache(cache_obj=_cache)

        _initialized = True
        log.info(f"GPTCache initialized (semantic mode, db={settings.cache_db_path})")

    except ImportError as e:
        log.warning(f"GPTCache not available: {e}")
    except Exception as e:
        log.error(f"Failed to initialize GPTCache: {e}")


def _extract_query(messages: list[dict]) -> str:
    """Extract the user's query text for cache key."""
    # Use the last user message as the cache key
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            return msg["content"]
    return json.dumps(messages, sort_keys=True)


def cache_lookup(messages: list[dict], model: str) -> dict | None:
    """Look up a cached response by semantic similarity."""
    if _cache is None:
        return None

    try:
        query = _extract_query(messages)
        result = _cache.get(query)
        if result is not None and result != "":
            log.info(f"Cache HIT for model={model}")
            if isinstance(result, str):
                return json.loads(result)
            return result
    except Exception as e:
        log.debug(f"Cache lookup error: {e}")

    return None


def cache_store(messages: list[dict], model: str, response_dict: dict) -> None:
    """Store a response in the cache keyed by the user's query."""
    if _cache is None:
        return

    try:
        query = _extract_query(messages)
        value = json.dumps(response_dict, default=str)
        _cache.set(query, value)
        log.debug(f"Cache STORE for model={model}")
    except Exception as e:
        log.debug(f"Cache store error: {e}")


def is_cacheable(stream: bool, tools: list | None) -> bool:
    """Check if a request is eligible for caching."""
    return not stream and not tools


# ---------------------------------------------------------------------------
# P1-7: TaskResponseCache — per-task-type TTL, exact content-hash key
# ---------------------------------------------------------------------------


class TaskResponseCache:
    """In-memory response cache keyed by content hash with per-task-type TTL.

    Used as a fast path before external provider calls (non-streaming only).
    Key: SHA-256 of (atlas_model + last 3 non-system messages).
    Value: unmasked assistant text string.

    TTLs are tuned per task type — creative tasks expire quickly (answers
    should vary), structured tasks (code, extraction) can be cached longer.
    """

    _TASK_TTL: dict[str, int] = {
        "chat": 300,  # 5 min
        "general": 300,  # 5 min
        "code": 1800,  # 30 min
        "structured_extraction": 3600,  # 60 min
        "analysis": 600,  # 10 min
        "summarization": 1800,  # 30 min
        "translation": 3600,  # 60 min
        "math": 1800,  # 30 min
        "creative": 120,  # 2 min (creative answers should vary)
        "infra": 1800,  # 30 min
    }
    _DEFAULT_TTL = 600  # 10 min

    def __init__(self, max_size: int = 500):
        self._store: dict[str, tuple[str, float]] = {}  # key → (text, expires_at)
        self._max_size = max_size

    def _make_key(self, atlas_model: str, messages: list[dict]) -> str:
        # Use last 3 non-system messages as the semantic fingerprint
        tail = [m for m in messages if m.get("role") != "system"][-3:]
        raw = json.dumps({"m": atlas_model, "t": tail}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, atlas_model: str, messages: list[dict]) -> str | None:
        """Return cached text or None if missing/expired."""
        key = self._make_key(atlas_model, messages)
        entry = self._store.get(key)
        if entry is None:
            return None
        text, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return text

    # Substrings that indicate the response is a CLI/auth error, not real content.
    _ERROR_MARKERS = (
        "not logged in",
        "please run /login",
        "authentication failed",
        "api key",
        "rate limit exceeded",
    )

    def put(self, atlas_model: str, messages: list[dict], text: str, task_type: str) -> None:
        """Store a response text with TTL derived from task_type."""
        if not text:
            return
        lower = text.lower()
        if any(marker in lower for marker in self._ERROR_MARKERS):
            return
        # Simple FIFO eviction when full
        if len(self._store) >= self._max_size:
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
        ttl = self._TASK_TTL.get(task_type, self._DEFAULT_TTL)
        key = self._make_key(atlas_model, messages)
        self._store[key] = (text, time.time() + ttl)

    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


# Singleton used by the Atlas router
task_cache = TaskResponseCache()
