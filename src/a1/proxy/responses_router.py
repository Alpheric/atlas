"""OpenAI Responses API endpoint: /v1/responses.

Thin adapter that handles OpenClaw-specific concerns (message cleanup,
heartbeats, auto-session, history-skip), then delegates to CorePipeline.
"""

import hashlib
import time
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.auth import hash_key, verify_api_key
from a1.common.logging import get_logger
from a1.common.metrics import metrics
from a1.db.repositories import ConversationRepo, MessageRepo, RoutingRepo
from a1.dependencies import get_db
from a1.proxy.core_pipeline import CorePipelineInput, core_pipeline, request_id_var
from a1.proxy.pipeline import _return_response_or_stream
from a1.proxy.request_models import MessageInput

log = get_logger("proxy.responses")
router = APIRouter()


# ---------------------------------------------------------------------------
# OpenClaw-specific helpers (kept here, not in CorePipeline)
# ---------------------------------------------------------------------------


def _match_fast_response(text: str, is_heartbeat_only: bool) -> dict | None:
    """Match simple queries for instant cached responses."""
    if is_heartbeat_only:
        return {"text": "HEARTBEAT_OK", "label": "heartbeat"}

    if not text or len(text.split()) > 15:
        return None

    if "respond with a status update" in text:
        return {
            "text": "**Status:** Online & Operational\nAtlas by Alpheric.AI -- ready for tasks.",
            "label": "agent_status",
        }

    _identity = (
        "I'm **Atlas**, an AI assistant built by **Alpheric.AI**. "
        "I handle planning, code, security, infrastructure, data, writing, "
        "and compliance tasks across the Atlas model family. How can I help you today?"
    )
    for p in (
        "who are you",
        "what are you",
        "your name",
        "what is your name",
        "who r u",
        "introduce yourself",
        "tell me about yourself",
    ):
        if p in text:
            return {"text": _identity, "label": "identity"}

    if len(text.split()) <= 4:
        for p in (
            "hello",
            "hi",
            "hey",
            "hii",
            "hiii",
            "good morning",
            "good afternoon",
            "good evening",
            "howdy",
            "sup",
        ):
            if text.strip().rstrip("!.,") in (p, f"{p} there", f"{p} atlas", f"{p} alpheric"):
                return {
                    "text": "Hello! I'm Atlas by Alpheric.AI. How can I help you today?",
                    "label": "greeting",
                }

    for p in (
        "how are you",
        "how r u",
        "how do you do",
        "what's up",
        "whats up",
        "how is it going",
        "how are things",
    ):
        if p in text:
            return {
                "text": (
                    "I'm running great, thanks for asking! "
                    "Atlas is online and all systems are healthy. What can I help you with?"
                ),
                "label": "smalltalk",
            }

    return None


def _clean_openclaw_messages(messages: list) -> list:
    """Clean OpenClaw payloads: strip heartbeats, deduplicate, trim to 10 turns."""
    sys_msgs = [m for m in messages if m.role == "system"]
    non_sys = [m for m in messages if m.role != "system"]
    if not non_sys:
        return messages

    heartbeat_msgs, real_msgs = [], []
    for m in non_sys:
        raw = m.content or ""
        content = (
            raw
            if isinstance(raw, str)
            else " ".join(p.get("text", "") for p in raw if isinstance(p, dict))
        ).lower()
        is_noise = (
            "heartbeat" in content and ("heartbeat.md" in content or "heartbeat_ok" in content)
        ) or (content.startswith("system:") and "gateway restart" in content)
        (heartbeat_msgs if is_noise else real_msgs).append(m)

    cleaned = real_msgs if real_msgs else heartbeat_msgs

    # Deduplicate consecutive identical
    deduped = []
    for m in cleaned:
        if deduped and deduped[-1].role == m.role and deduped[-1].content == m.content:
            continue
        deduped.append(m)

    max_turns = 10
    if len(deduped) > max_turns * 2:
        deduped = deduped[-(max_turns * 2) :]

    return sys_msgs + deduped


def _msg_text(m) -> str:
    raw = m.content or ""
    if isinstance(raw, list):
        return " ".join(p.get("text", "") for p in raw if isinstance(p, dict))
    return raw


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/responses")
async def responses_api(
    request: dict,
    response: Response,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """OpenAI Responses API format -- used by OpenClaw and other clients."""
    start_time = time.time()
    api_key_hash = hash_key(api_key) if api_key != "dev" else None
    rid = request_id_var.get("")

    # Parse input
    model = request.get("model", "atlas-plan")
    input_data = request.get("input", "")
    instructions = request.get("instructions", "")
    tools = request.get("tools", [])
    temperature = request.get("temperature")
    max_tokens = request.get("max_output_tokens") or request.get("max_tokens", 1000)
    stream = request.get("stream", False)

    # Build messages
    messages = []
    system_parts = []
    if instructions:
        system_parts.append(instructions)
    if tools:
        tool_descs = []
        for t in tools:
            name = t.get("name", t.get("function", {}).get("name", "unknown"))
            desc = t.get("description", t.get("function", {}).get("description", ""))
            tool_descs.append(f"- {name}: {desc}")
        if tool_descs:
            system_parts.append("\n\nAvailable tools:\n" + "\n".join(tool_descs))
    if system_parts:
        messages.append(MessageInput(role="system", content="\n".join(system_parts)))

    if isinstance(input_data, str):
        messages.append(MessageInput(role="user", content=input_data))
    elif isinstance(input_data, list):
        for msg in input_data:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                messages.append(MessageInput(role=role, content=content))
            elif isinstance(msg, str):
                messages.append(MessageInput(role="user", content=msg))
    if not messages:
        messages.append(MessageInput(role="user", content="Hello"))

    # OpenClaw cleanup
    messages = _clean_openclaw_messages(messages)

    # Fast-path check (before CorePipeline)
    user_msgs = [m for m in messages if m.role == "user"]
    is_heartbeat_only = (
        all("heartbeat" in _msg_text(m).lower() for m in user_msgs) if user_msgs else False
    )

    last_user_text = ""
    for m in reversed(user_msgs):
        t = _msg_text(m).strip()
        if "GMT+5:30]" in t:
            t = t.split("GMT+5:30]")[-1].strip()
        if t and "heartbeat" not in t.lower():
            last_user_text = t.lower()
            break

    fast = _match_fast_response(last_user_text, is_heartbeat_only)
    if fast is not None:
        resp_id = f"resp_{uuid.uuid4().hex[:12]}"
        latency_ms = int((time.time() - start_time) * 1000)
        log.info(f"[responses] Fast-path: {fast['label']} ({latency_ms}ms)")
        metrics.record_request(
            "cache",
            fast["label"],
            fast["label"],
            latency_ms,
            0.0,
            0,
            len(fast["text"].split()),
            is_local=True,
        )
        return await _return_response_or_stream(
            stream,
            resp_id,
            model,
            fast["text"],
            {
                "input_tokens": 0,
                "output_tokens": len(fast["text"].split()),
                "total_tokens": len(fast["text"].split()),
            },
            {"provider": "cache", "is_local": True, "task_type": fast["label"], "fast_path": True},
        )

    # Session resolution — priority order:
    # 1. explicit session_id (atlas-ai passes prior run's session key here for continuity)
    # 2. session_key (first-run key; registered as a new session under this ID)
    # 3. previous_response_id (OpenClaw response chaining)
    session_id = request.get("session_id") or request.get("session_key")
    previous_response_id = request.get("previous_response_id")
    if not session_id and isinstance(input_data, list):
        _first_raw = next(
            (
                m.get("content", "")
                for m in input_data
                if isinstance(m, dict) and m.get("role") == "user"
            ),
            "",
        )
        _first = (
            " ".join(p.get("text", "") for p in _first_raw if isinstance(p, dict))
            if isinstance(_first_raw, list)
            else _first_raw or ""
        )
        if "openclaw" in _first.lower():
            from a1.common.tz import now_ist

            day_key = now_ist().strftime("%Y-%m-%d")
            session_id = hashlib.sha256(f"openclaw:{api_key_hash}:{day_key}".encode()).hexdigest()[
                :16
            ]

    # Determine if client sent full history (skip injection)
    non_sys_count = sum(1 for m in messages if m.role != "system")
    skip_history = non_sys_count > 4

    # Raw user input for session save
    raw_user_input = (
        input_data
        if isinstance(input_data, str)
        else (
            next(
                (
                    m.get("content", "")
                    for m in reversed(input_data)
                    if isinstance(m, dict) and m.get("role") == "user"
                ),
                "",
            )
            if isinstance(input_data, list)
            else ""
        )
    )

    # Delegate to CorePipeline
    inp = CorePipelineInput(
        request_id=rid or f"resp_{uuid.uuid4().hex[:12]}",
        source="responses",
        api_key_hash=api_key_hash,
        messages=messages,
        raw_user_input=raw_user_input,
        model=model,
        strategy="best_quality",
        temperature=temperature,
        max_tokens=max_tokens,
        stream=stream,
        session_id=session_id,
        previous_response_id=previous_response_id,
        user_id=request.get("user"),
        skip_history_injection=skip_history,
    )

    result = await core_pipeline.execute(inp, response)

    # Headers
    response.headers["X-A1-Provider"] = result.provider_name or "unknown"
    response.headers["X-A1-Model"] = result.model_name or model
    response.headers["X-A1-Is-Local"] = str(result.is_local).lower()

    # Error
    if result.error and not result.assistant_text:
        return {
            "id": result.response_id,
            "object": "response",
            "status": "failed",
            "error": {"type": result.error_type or "internal_error", "message": result.error},
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    # Streaming
    if result.chunk_iterator:
        meta = {
            "provider": result.provider_name,
            "is_local": result.is_local,
            "task_type": result.task_type,
            "atlas_model": result.atlas_model,
            "distillation": result.distillation,
            "session_id": result.session_id,
        }
        return await _return_response_or_stream(
            True,
            result.response_id,
            result.atlas_model or result.model_name or model,
            None,
            {},
            meta,
            chunk_iterator=result.chunk_iterator,
        )

    # Persist conversation (background-safe)
    try:
        conv_repo = ConversationRepo(db)
        msg_repo = MessageRepo(db)
        routing_repo = RoutingRepo(db)
        conv = await conv_repo.create(source="proxy", user_id=request.get("user"))
        seq = 0
        for m in messages:
            await msg_repo.add(conv.id, m.role, m.content or "", seq)
            seq += 1
        asst_msg = await msg_repo.add(conv.id, "assistant", result.assistant_text or "", seq)
        await routing_repo.record(
            message_id=asst_msg.id,
            provider=result.provider_name,
            model=result.model_name or model,
            strategy=result.strategy,
            task_type=result.task_type,
            confidence=result.confidence,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            is_local=result.is_local,
            api_key_hash=api_key_hash,
            self_healed=result.self_healed,
            heal_score_before=result.quality_score if result.self_healed else None,
        )
        # Fire-and-forget quality signal persist
        if not result.cache_hit and result.quality_score > 0:
            import asyncio as _asyncio

            from a1.healing.quality_scorer import score_and_store as _score_and_store

            _asyncio.create_task(
                _score_and_store(result.assistant_text or "", result.task_type, str(asst_msg.id))
            )
    except Exception as e:
        log.error(f"Failed to persist conversation: {e}")

    # Return Responses API format
    usage = {
        "input_tokens": result.prompt_tokens,
        "output_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
    }
    meta = {
        "provider": result.provider_name,
        "is_local": result.is_local,
        "task_type": result.task_type,
        "latency_ms": result.latency_ms,
        "session_id": result.session_id,
    }
    return await _return_response_or_stream(
        False,
        result.response_id,
        result.atlas_model or result.model_name or model,
        result.assistant_text or "",
        usage,
        meta,
    )
