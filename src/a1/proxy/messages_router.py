"""Anthropic Messages API: POST /v1/messages

Fully compatible with Claude Code CLI, Cline-Anthropic, Zed-Anthropic, and the
official Anthropic Python/TypeScript SDKs.

Wire format:
  Request:  Anthropic Messages API shape (system, messages w/ content blocks,
            tools w/ input_schema, tool_choice, stop_sequences, stream, etc.)
  Response: Anthropic MessagesResponse shape (content blocks, stop_reason,
            usage.input_tokens/output_tokens)
  SSE:      Anthropic event sequence (message_start → content_block_start →
            ping → content_block_delta* → content_block_stop →
            message_delta → message_stop)
  Auth:     x-api-key header (Anthropic format); also accepts Authorization: Bearer
  Errors:   {"type":"error","error":{"type":"...","message":"..."}}
"""

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.auth import hash_key
from a1.common.logging import get_logger
from a1.db.repositories import ConversationRepo, MessageRepo, RoutingRepo
from a1.dependencies import get_db
from a1.proxy.core_pipeline import CorePipelineInput, core_pipeline, request_id_var
from a1.proxy.request_models import FunctionDef, MessageInput, ToolDef
from config.settings import settings

log = get_logger("proxy.messages")
router = APIRouter()

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def _verify_key(
    x_api_key: str | None,
    authorization: str | None,
) -> str:
    """Verify x-api-key (Anthropic) or Authorization: Bearer (OpenAI).

    Returns the raw key string. Raises HTTPException with Anthropic error shape
    on failure so clients parse errors correctly.
    """
    from a1.common.auth import _enforce_rate_limit, _resolve_key_info

    if not settings.api_keys:
        return "dev"

    # Prefer x-api-key; fall back to Bearer token
    key = x_api_key
    if not key and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            key = parts[1]

    if not key:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "Missing API key. Provide x-api-key header.",
                },
            },
        )

    if key not in settings.api_keys:
        raise HTTPException(
            status_code=403,
            detail={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "Invalid API key.",
                },
            },
        )

    key_h = hash_key(key)
    _, _, rate_limit = await _resolve_key_info(key_h)
    try:
        await _enforce_rate_limit(key_h, rate_limit)
    except HTTPException as e:
        raise HTTPException(
            status_code=429,
            detail={
                "type": "error",
                "error": {
                    "type": "rate_limit_error",
                    "message": e.detail,
                },
            },
        ) from e

    return key


# ---------------------------------------------------------------------------
# Content block normalisation
# ---------------------------------------------------------------------------


def _blocks_to_text(content: Any) -> str:
    """Flatten Anthropic content (str or block array) to plain string.

    Handles: text, tool_result (string or nested blocks), document.
    Skips:   image, tool_use (assistant-turn only).
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type", "")
                if t == "text":
                    parts.append(block.get("text", ""))
                elif t == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        inner_texts = [
                            b.get("text", "")
                            for b in inner
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        parts.append("\n".join(t for t in inner_texts if t))
                    elif isinstance(inner, str):
                        parts.append(inner)
                elif t == "document":
                    parts.append(block.get("text", block.get("content", "")))
                # tool_use / image blocks are silently skipped
        return "\n".join(p for p in parts if p)
    return str(content)


def _parse_system(system: Any) -> str | None:
    """Parse system field: string OR array of content blocks."""
    if not system:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = [
            b.get("text", "")
            for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p) or None
    return str(system)


def _parse_messages(raw: list[dict]) -> list[MessageInput]:
    """Convert Anthropic messages array → MessageInput list.

    Each message's content may be a string or a block array; we flatten to str.
    """
    msgs: list[MessageInput] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        content = _blocks_to_text(m.get("content", ""))
        msgs.append(MessageInput(role=role, content=content))
    return msgs


# ---------------------------------------------------------------------------
# Tool schema translation
# ---------------------------------------------------------------------------


def _anthropic_tools_to_openai(tools: list[dict]) -> list[ToolDef]:
    """Anthropic tool schema → OpenAI ToolDef.

    Anthropic: {"name": "...", "description": "...", "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    result: list[ToolDef] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        result.append(
            ToolDef(
                type="function",
                function=FunctionDef(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    parameters=t.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                ),
            )
        )
    return result


def _translate_tool_choice(raw: Any) -> str | dict | None:
    """Anthropic tool_choice → OpenAI tool_choice."""
    if not raw:
        return None
    if isinstance(raw, str):
        return raw  # "auto" | "any" — pass through
    if isinstance(raw, dict):
        tc_type = raw.get("type", "auto")
        if tc_type == "tool":
            return {"type": "function", "function": {"name": raw.get("name", "")}}
        if tc_type == "any":
            return "required"  # OpenAI uses "required" for "must use a tool"
        return tc_type  # "auto"
    return None


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _build_content_blocks(text: str | None, tool_calls: list[dict] | None) -> list[dict]:
    """Build Anthropic content block list from pipeline result."""
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    if tool_calls:
        for tc in tool_calls:
            fn = tc.get("function", tc)
            try:
                input_data = json.loads(fn.get("arguments", "{}"))
            except Exception:
                input_data = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                    "name": fn.get("name", ""),
                    "input": input_data,
                }
            )
    return blocks or [{"type": "text", "text": ""}]


def _stop_reason(tool_calls: list | None) -> str:
    return "tool_use" if tool_calls else "end_turn"


def _make_response(
    response_id: str,
    model: str,
    text: str | None,
    tool_calls: list[dict] | None,
    stop_reason: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    return {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "content": _build_content_blocks(text, tool_calls),
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def _make_error(error_type: str, message: str) -> dict:
    return {"type": "error", "error": {"type": error_type, "message": message}}


# ---------------------------------------------------------------------------
# Anthropic SSE streaming
# ---------------------------------------------------------------------------


async def _sse_messages_stream(
    response_id: str,
    model: str,
    chunk_iterator,
    input_tokens: int,
) -> StreamingResponse:
    """Emit Anthropic Messages API SSE event sequence.

    Events emitted (in order):
      1. message_start
      2. content_block_start  (index 0, text block)
      3. ping
      4. content_block_delta* (one per token; also pings every 10s)
      5. content_block_stop
      6. message_delta        (stop_reason + output token count)
      7. message_stop
    """

    def _evt(event_type: str, data: dict) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    async def generate():
        # 1. message_start
        yield _evt(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": response_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": input_tokens, "output_tokens": 1},
                },
            },
        )

        # 2. content_block_start
        yield _evt(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )

        # 3. initial ping
        yield _evt("ping", {"type": "ping"})

        # 4. stream deltas
        output_tokens = 0
        last_ping = time.monotonic()
        PING_INTERVAL = 10.0

        async for chunk in chunk_iterator:
            # Periodic ping to prevent proxy/client timeouts on long generations
            now = time.monotonic()
            if now - last_ping >= PING_INTERVAL:
                yield _evt("ping", {"type": "ping"})
                last_ping = now

            if chunk.choices and chunk.choices[0].delta.content:
                delta_text = chunk.choices[0].delta.content
                output_tokens += 1  # rough count — provider may send usage in final chunk
                yield _evt(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": delta_text},
                    },
                )

            # Use accurate token count from provider's final usage chunk
            if chunk.usage:
                output_tokens = chunk.usage.completion_tokens

        # 5. content_block_stop
        yield _evt("content_block_stop", {"type": "content_block_stop", "index": 0})

        # 6. message_delta
        yield _evt(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )

        # 7. message_stop
        yield _evt("message_stop", {"type": "message_stop"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/messages")
async def messages_api(
    request: Request,
    response: Response,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    authorization: str | None = Header(default=None, alias="authorization"),
    anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    anthropic_beta: str | None = Header(default=None, alias="anthropic-beta"),
    db: AsyncSession = Depends(get_db),
):
    """Anthropic Messages API — compatible with Claude Code, Cline, Zed, Anthropic SDK."""
    api_key = await _verify_key(x_api_key, authorization)
    rid = request_id_var.get("")

    # --- Parse body ---
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=_make_error("invalid_request_error", "Request body must be valid JSON."),
        )

    model = body.get("model", "atlas-plan")
    max_tokens = body.get("max_tokens", 1024)
    temperature = body.get("temperature")
    stream = body.get("stream", False)

    # system (string or block array)
    system_text = _parse_system(body.get("system"))

    # messages
    raw_messages = body.get("messages", [])
    if not raw_messages:
        raise HTTPException(
            status_code=400,
            detail=_make_error("invalid_request_error", "'messages' must be a non-empty array."),
        )

    pipeline_messages = _parse_messages(raw_messages)

    # Prepend system as a special system role message
    if system_text:
        pipeline_messages = [MessageInput(role="system", content=system_text)] + pipeline_messages

    # Must have at least one non-system turn
    if not any(m.role != "system" for m in pipeline_messages):
        raise HTTPException(
            status_code=400,
            detail=_make_error("invalid_request_error", "'messages' must contain at least one user turn."),
        )

    # Raw user input for session tracking
    raw_user_input = next(
        (m.content for m in reversed(pipeline_messages) if m.role == "user"), ""
    ) or ""

    # tools (Anthropic → OpenAI format)
    raw_tools = body.get("tools")
    tools = _anthropic_tools_to_openai(raw_tools) if raw_tools else None

    # tool_choice
    tool_choice = _translate_tool_choice(body.get("tool_choice"))

    # Build CorePipelineInput
    api_key_hash = hash_key(api_key) if api_key != "dev" else None
    inp = CorePipelineInput(
        request_id=rid or f"msg_{uuid.uuid4().hex[:12]}",
        source="anthropic",
        api_key_hash=api_key_hash,
        messages=pipeline_messages,
        raw_user_input=raw_user_input,
        model=model,
        strategy="best_quality",
        temperature=temperature,
        max_tokens=max_tokens,
        stream=stream,
        tools=tools,
        tool_choice=tool_choice,
    )

    # --- Execute ---
    result = await core_pipeline.execute(inp, response)

    # Propagate Atlas metadata headers
    response.headers["X-A1-Provider"] = result.provider_name or "unknown"
    response.headers["X-A1-Model"] = result.model_name or model
    response.headers["X-A1-Is-Local"] = str(result.is_local).lower()
    if result.cost_usd:
        response.headers["X-A1-Cost"] = str(round(result.cost_usd, 6))

    # --- Error ---
    if result.error and not result.assistant_text:
        status_code = 529 if result.error_type == "rate_limit_error" else (
            503 if result.error_type == "provider_error" else 500
        )
        raise HTTPException(
            status_code=status_code,
            detail=_make_error(
                "overloaded_error" if status_code == 503 else "api_error",
                result.error,
            ),
        )

    # --- Streaming ---
    if result.chunk_iterator:
        return await _sse_messages_stream(
            result.response_id,
            result.model_name or model,
            result.chunk_iterator,
            result.prompt_tokens,
        )

    # --- Non-streaming ---
    # Extract tool_calls from raw_response if available
    tool_calls = None
    if result.raw_response:
        try:
            tool_calls = result.raw_response.choices[0].message.tool_calls
        except (AttributeError, IndexError):
            pass

    # --- Persist conversation to DB (so it appears in dashboard) ---
    try:
        conv_repo = ConversationRepo(db)
        msg_repo = MessageRepo(db)
        routing_repo = RoutingRepo(db)

        conv = await conv_repo.create(source="anthropic", user_id=body.get("user"))
        seq = 0
        for m in pipeline_messages:
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

    return _make_response(
        response_id=result.response_id,
        model=result.model_name or model,
        text=result.assistant_text,
        tool_calls=tool_calls,
        stop_reason=_stop_reason(tool_calls),
        input_tokens=result.prompt_tokens,
        output_tokens=result.completion_tokens,
    )
