"""Session Manager — maintains conversation context across requests.

Tracks multi-turn conversations with in-memory cache + optional Redis persistence.
Sessions expire after configurable TTL. Supports session chaining via
previous_response_id (OpenClaw pattern) or explicit session_id.

When Redis is available (A1_REDIS_URL configured), sessions survive server restarts.
Falls back to in-memory-only mode if Redis is unavailable.
"""

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from a1.common.logging import get_logger

log = get_logger("session")

_REDIS_SESSION_PREFIX = "a1:session:"
_REDIS_RESP_PREFIX = "a1:resp:"


@dataclass
class SessionMessage:
    """A single message in a session."""

    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    response_id: str | None = None  # links response to session for chaining


@dataclass
class Session:
    """Active conversation session."""

    id: str
    user_id: str | None = None
    messages: list[SessionMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    # ── Routing stickiness state ───────────────────────────────────────────
    # Persisted across turns so the pipeline can re-use the same model.
    primary_model: str | None = None  # e.g. "atlas-plan" or "claude-sonnet-4-20250514"
    primary_provider: str | None = None  # e.g. "claude-cli" or "vertex"
    primary_task_type: str | None = None  # e.g. "planning", "coding"
    routing_mode: str = "balanced"  # quality / balanced / low_cost / local_first
    fallback_count: int = 0  # how many times we've fallen back this session

    def set_routing(self, model: str, provider: str, task_type: str) -> None:
        """Record the primary model chosen this turn (called after first successful response)."""
        self.primary_model = model
        self.primary_provider = provider
        self.primary_task_type = task_type
        self.last_activity = time.time()

    def record_fallback(self, new_model: str, new_provider: str, reason: str) -> None:
        """Record a mid-session fallback. Called by the pipeline on failure."""
        self.fallback_count += 1
        self.primary_model = new_model
        self.primary_provider = new_provider
        self.metadata[f"fallback_{self.fallback_count}"] = {
            "model": new_model,
            "provider": new_provider,
            "reason": reason,
            "at": time.time(),
        }

    def add_message(self, role: str, content: str, response_id: str | None = None):
        self.messages.append(
            SessionMessage(
                role=role,
                content=content,
                response_id=response_id,
            )
        )
        self.last_activity = time.time()

    def get_history(self, limit: int = 20) -> list[dict]:
        """Get last N messages as dicts for injection into prompts."""
        recent = self.messages[-limit:] if limit else self.messages
        return [{"role": m.role, "content": m.content} for m in recent]

    def is_expired(self, ttl_seconds: int) -> bool:
        return time.time() - self.last_activity > ttl_seconds

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "response_id": m.response_id,
                }
                for m in self.messages
            ],
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "metadata": self.metadata,
            # Routing stickiness
            "primary_model": self.primary_model,
            "primary_provider": self.primary_provider,
            "primary_task_type": self.primary_task_type,
            "routing_mode": self.routing_mode,
            "fallback_count": self.fallback_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        s = cls(
            id=data["id"],
            user_id=data.get("user_id"),
            created_at=data.get("created_at", time.time()),
            last_activity=data.get("last_activity", time.time()),
            metadata=data.get("metadata", {}),
        )
        # Routing stickiness
        s.primary_model = data.get("primary_model")
        s.primary_provider = data.get("primary_provider")
        s.primary_task_type = data.get("primary_task_type")
        s.routing_mode = data.get("routing_mode", "balanced")
        s.fallback_count = data.get("fallback_count", 0)
        for m in data.get("messages", []):
            s.messages.append(
                SessionMessage(
                    role=m["role"],
                    content=m["content"],
                    timestamp=m.get("timestamp", s.created_at),
                    response_id=m.get("response_id"),
                )
            )
        return s


class RedisSessionBackend:
    """Optional Redis backend for durable session persistence across restarts.

    Uses redis.asyncio for non-blocking I/O. All errors are caught and logged —
    the session manager falls back to in-memory-only when Redis is unavailable.
    """

    def __init__(self, redis_url: str, ttl_seconds: int = 3600):
        self._redis_url = redis_url
        self._ttl = ttl_seconds
        self._client = None
        self.available = False

    async def init(self):
        """Connect to Redis and verify availability."""
        try:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            await self._client.ping()
            self.available = True
            log.info(f"Redis session backend connected: {self._redis_url}")
        except Exception as e:
            self.available = False
            log.warning(f"Redis unavailable — sessions are in-memory only: {e}")

    async def load(self, session_id: str) -> "Session | None":
        if not self.available or not self._client:
            return None
        try:
            raw = await self._client.get(f"{_REDIS_SESSION_PREFIX}{session_id}")
            if raw:
                return Session.from_dict(json.loads(raw))
        except Exception as e:
            log.warning(f"Redis session load failed for {session_id}: {e}")
        return None

    async def save(self, session: "Session"):
        if not self.available or not self._client:
            return
        try:
            key = f"{_REDIS_SESSION_PREFIX}{session.id}"
            await self._client.setex(key, self._ttl, json.dumps(session.to_dict()))
        except Exception as e:
            log.warning(f"Redis session save failed for {session.id}: {e}")

    async def load_by_response(self, response_id: str) -> "str | None":
        """Return the session_id linked to a response_id."""
        if not self.available or not self._client:
            return None
        try:
            return await self._client.get(f"{_REDIS_RESP_PREFIX}{response_id}")
        except Exception as e:
            log.warning(f"Redis resp lookup failed for {response_id}: {e}")
        return None

    async def link_response(self, response_id: str, session_id: str):
        if not self.available or not self._client:
            return
        try:
            await self._client.setex(f"{_REDIS_RESP_PREFIX}{response_id}", self._ttl, session_id)
        except Exception as e:
            log.warning(f"Redis resp link failed for {response_id}: {e}")


class SessionManager:
    """Manages conversation sessions with in-memory LRU cache.

    When a RedisSessionBackend is configured, sessions are written through to
    Redis so they survive server restarts. On a cold start, sessions are loaded
    from Redis on first access (lazy restore).

    Thread-safe for async usage. Sessions are evicted from memory when:
    - TTL expires (configurable, default 1 hour)
    - Cache exceeds max_sessions (LRU eviction)
    """

    def __init__(self, ttl_seconds: int = 3600, max_sessions: int = 1000, max_messages: int = 20):
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._response_to_session: dict[str, str] = {}  # response_id → session_id
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self._redis: RedisSessionBackend | None = None

    async def init_redis(self, redis_url: str):
        """Initialize the Redis backend. Called once at application startup."""
        backend = RedisSessionBackend(redis_url, ttl_seconds=self.ttl_seconds)
        await backend.init()
        if backend.available:
            self._redis = backend

    async def get_or_create(
        self,
        session_id: str | None = None,
        previous_response_id: str | None = None,
        user_id: str | None = None,
    ) -> Session:
        """Get existing session or create new one.

        Resolution order:
        1. Explicit session_id → lookup in memory, then Redis
        2. previous_response_id → find session that produced that response
        3. Neither → create new session

        New/restored sessions are written through to Redis when available.
        """
        self._cleanup_expired()

        # 1. Explicit session_id — check memory first, then Redis
        if session_id:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                self._sessions.move_to_end(session_id)
                session.last_activity = time.time()
                return session
            if self._redis:
                session = await self._redis.load(session_id)
                if session and not session.is_expired(self.ttl_seconds):
                    self._sessions[session_id] = session
                    self._sessions.move_to_end(session_id)
                    session.last_activity = time.time()
                    log.info(f"Session restored from Redis: {session_id[:8]}...")
                    return session

        # 2. Chain via previous_response_id — check memory map, then Redis
        if previous_response_id:
            sid = self._response_to_session.get(previous_response_id)
            if not sid and self._redis:
                sid = await self._redis.load_by_response(previous_response_id)
                if sid:
                    self._response_to_session[previous_response_id] = sid
            if sid and sid in self._sessions:
                session = self._sessions[sid]
                self._sessions.move_to_end(sid)
                session.last_activity = time.time()
                return session
            if sid and self._redis:
                session = await self._redis.load(sid)
                if session and not session.is_expired(self.ttl_seconds):
                    self._sessions[sid] = session
                    session.last_activity = time.time()
                    return session

        # 3. Create new session
        new_id = session_id or str(uuid.uuid4())
        session = Session(id=new_id, user_id=user_id)
        self._sessions[new_id] = session

        # Evict oldest if over limit
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

        log.info(f"New session: {new_id[:8]}... (user={user_id})")

        if self._redis:
            asyncio.create_task(self._redis.save(session))

        return session

    async def link_response(self, response_id: str, session_id: str):
        """Link a response_id to a session for future chaining."""
        self._response_to_session[response_id] = session_id
        # Keep map bounded
        if len(self._response_to_session) > self.max_sessions * 10:
            keys = list(self._response_to_session.keys())
            for k in keys[: len(keys) // 2]:
                del self._response_to_session[k]
        if self._redis:
            asyncio.create_task(self._redis.link_response(response_id, session_id))

    def get_session(self, session_id: str) -> Session | None:
        """Get session by ID from memory, or None if not found/expired."""
        session = self._sessions.get(session_id)
        if session and not session.is_expired(self.ttl_seconds):
            return session
        return None

    def list_active(self) -> list[dict]:
        """List all active sessions for dashboard."""
        self._cleanup_expired()
        return [
            {
                "id": s.id,
                "user_id": s.user_id,
                "message_count": len(s.messages),
                "created_at": s.created_at,
                "last_activity": s.last_activity,
                "age_seconds": int(time.time() - s.created_at),
            }
            for s in self._sessions.values()
        ]

    def _cleanup_expired(self):
        """Remove expired sessions from memory."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired(self.ttl_seconds)]
        for sid in expired:
            del self._sessions[sid]
            log.debug(f"Session expired: {sid[:8]}...")


# Singleton
session_manager = SessionManager()
