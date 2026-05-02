"""Shared pipeline helpers used by all proxy handlers."""

import asyncio
import copy
import json
import re
import time
import uuid

from a1.common.logging import get_logger
from config.settings import settings

log = get_logger("proxy")

# ---------------------------------------------------------------------------
# 4.3 — Strip deepseek-r1 reasoning tokens before returning to user
# ---------------------------------------------------------------------------
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_think_tokens(text: str) -> str:
    """Remove <think>...</think> blocks produced by deepseek-r1 style models."""
    return _THINK_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# 4.1 — Server-side tool registry + ReAct execution loop
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Registry of callables that can be executed server-side in the ReAct loop."""

    def __init__(self):
        self._tools: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self._tools[name] = fn

    def has(self, name: str) -> bool:
        return name in self._tools

    async def execute(self, name: str, arguments: dict) -> str:
        fn = self._tools.get(name)
        if fn is None:
            return f"Error: tool '{name}' is not registered"
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(**arguments)
            else:
                result = fn(**arguments)
            return str(result)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"


tool_registry = ToolRegistry()


async def execute_tool_loop(provider, request, max_iterations: int = 5):
    """ReAct tool execution loop.

    Calls provider, executes tool_calls via tool_registry, injects tool_results,
    and re-calls until a text response is returned or max_iterations is reached.
    Returns the final ChatCompletionResponse.
    """
    from a1.proxy.request_models import MessageInput

    req = copy.deepcopy(request)

    for iteration in range(max_iterations):
        result = await provider.complete(req)

        if not result.choices:
            break

        choice = result.choices[0]
        tool_calls = choice.message.tool_calls

        if not tool_calls:
            return result  # text response — done

        # Append assistant message with tool_calls
        req.messages.append(
            MessageInput(
                role="assistant",
                content=choice.message.content,
                tool_calls=tool_calls,
            )
        )

        # Execute each tool and inject result messages
        for tc in tool_calls:
            tool_id = tc.get("id", f"call_{iteration}_{uuid.uuid4().hex[:6]}")
            fn_data = tc.get("function", tc)
            tool_name = fn_data.get("name", "")
            try:
                arguments = json.loads(fn_data.get("arguments", "{}"))
            except Exception:
                arguments = {}
            tool_result = await tool_registry.execute(tool_name, arguments)
            req.messages.append(
                MessageInput(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tool_id,
                )
            )

        log.debug(
            f"[tool_loop] iteration {iteration + 1}/{max_iterations}, ran {len(tool_calls)} tools"
        )

    # Iteration limit reached — do a final call with tools disabled
    req_final = copy.deepcopy(req)
    req_final.tools = None
    req_final.tool_choice = None
    return await provider.complete(req_final)


# Reference cost for savings calculation (gpt-4o-mini rates)
REFERENCE_COST_PER_1K_INPUT = 0.00015
REFERENCE_COST_PER_1K_OUTPUT = 0.0006

# Legacy model name aliases — applied once at request entry per handler
LEGACY_ALIASES: dict[str, str] = {
    "alpheric-1": "atlas-plan",
    # Cursor IDE / custom model name aliases
    "Atlas": "atlas-plan",
    "atlas": "atlas-plan",
    "atlas-default": "atlas-plan",
    "atlas-1": "atlas-plan",
}


async def _return_response_or_stream(
    stream: bool,
    resp_id: str,
    model: str,
    text: str,
    usage: dict,
    metadata: dict | None = None,
    chunk_iterator=None,
):
    """Return either JSON response or SSE stream based on stream flag.

    If chunk_iterator is provided and stream=True, streams tokens live
    as they arrive from the provider (true streaming, not buffered).
    """
    if stream:
        from a1.proxy.stream import sse_responses_stream_live

        return await sse_responses_stream_live(
            resp_id,
            model,
            chunk_iterator=chunk_iterator,
            full_text=text,
            usage=usage,
            metadata=metadata,
        )
    # session_key at top level — required by atlas-ai adapter for session continuity
    # (atlas-ai reads body.session_key, not body.metadata.session_id)
    _session_key = (metadata or {}).get("session_id", "") or ""
    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "session_key": _session_key,
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
                "status": "completed",
            }
        ],
        "status": "completed",
        "usage": usage,
        **({"metadata": metadata} if metadata else {}),
    }


def _calc_equivalent_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """What this request would have cost using the reference external model."""
    return (
        prompt_tokens / 1000 * REFERENCE_COST_PER_1K_INPUT
        + completion_tokens / 1000 * REFERENCE_COST_PER_1K_OUTPUT
    )


async def _persist_usage(
    provider_name: str,
    model_name: str,
    is_local: bool,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    latency_ms: int,
    api_key_hash: str | None,
    account_id=None,
    cache_hit: bool = False,
    error: bool = False,
):
    """Persist usage record to DB (fire-and-forget background task)."""
    try:
        from a1.db.engine import async_session
        from a1.db.models import UsageRecord

        async with async_session() as session:
            async with session.begin():
                record = UsageRecord(
                    api_key_hash=api_key_hash,
                    account_id=account_id,
                    provider=provider_name,
                    model=model_name,
                    is_local=is_local,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost,
                    equivalent_external_cost_usd=_calc_equivalent_cost(
                        prompt_tokens, completion_tokens
                    )
                    if is_local
                    else 0,
                    latency_ms=latency_ms,
                    error=error,
                    cache_hit=cache_hit,
                )
                session.add(record)
    except Exception as e:
        log.error(f"Failed to persist usage: {e}")


async def _load_session(
    session_id: str | None,
    previous_response_id: str | None,
    user_id: str | None,
    messages: list,
) -> tuple:
    """Prepend session history to messages. Returns (session_or_None, updated_messages).

    Applies two limits:
    1. Count-based: at most settings.session_max_messages history messages.
    2. Token-budget: if settings.session_max_history_tokens > 0, trims oldest history
       messages until the combined token count (system + history + current) stays under
       the budget — ensuring the context window never overflows.
    """
    if not settings.session_enabled:
        return None, messages
    from a1.common.tokens import count_messages_tokens_for_model
    from a1.proxy.request_models import MessageInput
    from a1.session.manager import session_manager

    session = await session_manager.get_or_create(
        session_id=session_id,
        previous_response_id=previous_response_id,
        user_id=user_id,
    )
    history = session.get_history(limit=settings.session_max_messages)
    if not history:
        return session, messages

    sys_msgs = [m for m in messages if m.role == "system"]
    non_sys = [m for m in messages if m.role != "system"]
    hist_msgs = [MessageInput(role=h["role"], content=h["content"]) for h in history]

    # Token-budget trimming: drop oldest messages until under budget
    token_budget = settings.session_max_history_tokens
    if token_budget > 0 and hist_msgs:
        all_msgs_dicts = (
            [{"role": m.role, "content": m.content or ""} for m in sys_msgs]
            + [{"role": m.role, "content": m.content or ""} for m in hist_msgs]
            + [{"role": m.role, "content": m.content or ""} for m in non_sys]
        )
        total = count_messages_tokens_for_model(all_msgs_dicts, "claude-sonnet-4-20250514")
        while hist_msgs and total > token_budget:
            removed = hist_msgs.pop(0)  # drop oldest history message
            total -= count_messages_tokens_for_model(
                [{"role": removed.role, "content": removed.content or ""}],
                "claude-sonnet-4-20250514",
            )
        if not hist_msgs:
            log.warning(
                f"[session] Context budget={token_budget} tokens exhausted by current messages; "
                "injecting no history"
            )

    return session, sys_msgs + hist_msgs + non_sys


def _mask_pii(messages: list) -> tuple:
    """Mask PII in messages. Returns (masked_messages, mask_map)."""
    if not settings.pii_masking_enabled:
        return messages, {}
    from a1.proxy.request_models import MessageInput
    from a1.security.pii_masker import pii_masker

    dicts = [{"role": m.role, "content": m.content or ""} for m in messages]
    masked, mask_map = pii_masker.mask_messages(dicts)
    return [MessageInput(role=d["role"], content=d["content"]) for d in masked], mask_map
