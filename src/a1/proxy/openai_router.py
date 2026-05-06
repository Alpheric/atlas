"""OpenAI-compatible endpoints: /v1/chat/completions and /v1/models.

Thin adapter that normalizes ChatCompletionRequest into CorePipelineInput,
delegates to CorePipeline.execute(), and formats the result as
ChatCompletionResponse.
"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.auth import hash_key, verify_api_key
from a1.common.logging import get_logger
from a1.db.repositories import ConversationRepo, MessageRepo, RoutingRepo
from a1.dependencies import get_db
from a1.providers.registry import provider_registry
from a1.proxy.core_pipeline import CorePipelineInput, core_pipeline, request_id_var
from a1.proxy.request_models import ChatCompletionRequest
from a1.proxy.response_models import ChatCompletionResponse, Choice, ChoiceMessage, Usage
from a1.proxy.stream import sse_stream

log = get_logger("proxy.openai")
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_conv_source(api_key_hash: str | None) -> tuple[str, str | None]:
    """Return (source_label, tenant_id) for the given key hash.

    Checks atlas_api_keys first (tenant/OneDesk keys), falls back to "proxy".
    source_label is used as Conversation.source so the dashboard can filter
    by origin: "onedesk", "proxy", etc.
    """
    if not api_key_hash:
        return "proxy", None
    try:
        from sqlalchemy import select
        from a1.db.engine import async_session
        from a1.db.models import AtlasApiKey
        async with async_session() as db:
            row = (await db.execute(
                select(AtlasApiKey.source, AtlasApiKey.tenant_id).where(
                    AtlasApiKey.key_hash == api_key_hash
                )
            )).first()
            if row:
                return row.source or "proxy", row.tenant_id
    except Exception:
        pass
    return "proxy", None


async def _persist_conversation(
    *,
    inp: CorePipelineInput,
    messages: list,
    assistant_text: str,
    result,
    api_key_hash: str | None,
    source: str,
    tenant_id: str | None,
    db: AsyncSession | None = None,
) -> None:
    """Write conversation + messages + routing record to DB.

    Called from both the streaming tail and the non-streaming path.
    When db is None (streaming case) a fresh session is opened.
    """
    from a1.db.engine import async_session as _mk_session

    async def _run(session: AsyncSession):
        conv_repo = ConversationRepo(session)
        msg_repo = MessageRepo(session)
        routing_repo = RoutingRepo(session)

        # Build metadata for dashboard attribution
        meta: dict = {}
        if tenant_id:
            meta["tenant_id"] = tenant_id

        conv_id = uuid.UUID(inp.conversation_id) if inp.conversation_id else None
        if not conv_id:
            conv = await conv_repo.create(
                source=source,
                user_id=inp.user_id,
            )
            # Store tenant attribution in metadata
            if meta:
                from sqlalchemy import update
                from a1.db.models import Conversation
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == conv.id)
                    .values(metadata_=meta)
                )
            conv_id = conv.id

        seq = 0
        for m in messages:
            content = m.content if hasattr(m, "content") else (m.get("content") or "")
            role = m.role if hasattr(m, "role") else m.get("role", "user")
            await msg_repo.add(conv_id, role, content, seq)
            seq += 1
        assistant_msg = await msg_repo.add(conv_id, "assistant", assistant_text, seq)

        await routing_repo.record(
            message_id=assistant_msg.id,
            provider=result.provider_name,
            model=result.model_name,
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

        # Quality signal
        if not result.cache_hit and result.quality_score > 0:
            from a1.healing.quality_scorer import score_and_store as _score_store
            asyncio.create_task(
                _score_store(assistant_text, result.task_type, str(assistant_msg.id))
            )

    try:
        if db is not None:
            await _run(db)
        else:
            async with _mk_session() as fresh_db:
                await _run(fresh_db)
                await fresh_db.commit()
    except Exception as e:
        log.error(f"Failed to persist conversation: {e}")


# ---------------------------------------------------------------------------
# Tool normalisation
# ---------------------------------------------------------------------------

def _normalize_tools(tools: list | None) -> tuple[list | None, bool]:
    """Expand OpenAI special tool types into function declarations Atlas can execute.

    Returns (normalized_tools_list, has_code_interpreter).
    `{"type": "code_interpreter"}` → full function declaration + registered handler.
    """
    if not tools:
        return tools, False

    from a1.tools.code_interpreter import TOOL_DECLARATION
    from a1.proxy.request_models import ToolDef, FunctionDef

    normalized: list = []
    has_ci = False
    for t in tools:
        tool_type = t.type if hasattr(t, "type") else t.get("type", "function")
        if tool_type == "code_interpreter":
            has_ci = True
            fn = TOOL_DECLARATION["function"]
            normalized.append(ToolDef(
                type="function",
                function=FunctionDef(
                    name=fn["name"],
                    description=fn["description"],
                    parameters=fn["parameters"],
                ),
            ))
        else:
            normalized.append(t)
    return normalized, has_ci


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    response: Response,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    api_key_hash = hash_key(api_key) if api_key != "dev" else None
    rid = request_id_var.get("")

    # Resolve source label + tenant attribution from the API key
    source, tenant_id = await _resolve_conv_source(api_key_hash)

    # Expand special tool types (code_interpreter) into function declarations
    normalized_tools, _has_ci = _normalize_tools(request.tools)

    # Build CorePipelineInput from OpenAI format
    inp = CorePipelineInput(
        request_id=rid or f"chatcmpl-{uuid.uuid4().hex[:12]}",
        source="openai",
        api_key_hash=api_key_hash,
        messages=list(request.messages),
        raw_user_input=next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        or "",
        model=request.model,
        strategy=request.strategy or "best_quality",
        temperature=request.temperature,
        max_tokens=request.max_tokens or 1000,
        stream=request.stream,
        tools=normalized_tools,
        tool_choice=request.tool_choice,
        session_id=request.session_id,
        previous_response_id=request.previous_response_id,
        user_id=request.user,
        conversation_id=request.conversation_id,
    )

    # Execute through unified pipeline
    result = await core_pipeline.execute(inp, response)

    # Set response headers
    response.headers["X-A1-Provider"] = result.provider_name or "unknown"
    response.headers["X-A1-Is-Local"] = str(result.is_local).lower()
    if result.cost_usd:
        response.headers["X-A1-Cost"] = str(round(result.cost_usd, 6))
    response.headers["X-A1-Cache"] = "hit" if result.cache_hit else "miss"

    # Handle errors
    if result.error and not result.assistant_text:
        raise HTTPException(
            status_code=503 if result.error_type == "provider_error" else 500,
            detail=result.error,
        )

    # ── Streaming ─────────────────────────────────────────────────────────────
    if result.chunk_iterator:
        from a1.common.tokens import count_messages_tokens_for_model, count_tokens_for_model

        messages_dicts = [m.model_dump(exclude_none=True) for m in request.messages]
        model_name = result.model_name or inp.model

        async def stream_and_log():
            full_content = ""
            stream_usage = None
            has_tool_calls = False

            async for chunk in result.chunk_iterator:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_content += delta.content
                    if delta.tool_calls:
                        has_tool_calls = True
                if chunk.usage:
                    stream_usage = chunk.usage
                yield chunk

            if stream_usage:
                pt = stream_usage.prompt_tokens
                ct = stream_usage.completion_tokens
            else:
                pt = count_messages_tokens_for_model(messages_dicts, model_name)
                ct = count_tokens_for_model(full_content, model_name)

            from a1.proxy.response_models import ChatCompletionChunk
            yield ChatCompletionChunk(
                id="chatcmpl-usage",
                model=model_name,
                choices=[],
                usage=Usage(prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct),
            )

            # Persist after streaming completes (tool-call turns carry no readable
            # text to store; skip them to avoid empty assistant messages)
            if full_content and not has_tool_calls:
                asyncio.create_task(
                    _persist_conversation(
                        inp=inp,
                        messages=request.messages,
                        assistant_text=full_content,
                        result=result,
                        api_key_hash=api_key_hash,
                        source=source,
                        tenant_id=tenant_id,
                        db=None,  # streaming: request DB session is gone, open fresh one
                    )
                )

        return await sse_stream(stream_and_log())

    # ── Non-streaming ─────────────────────────────────────────────────────────
    if result.raw_response and isinstance(result.raw_response, ChatCompletionResponse):
        # Distillation / tools path returns a ChatCompletionResponse directly
        resp = result.raw_response
        resp.provider = result.provider_name
        resp.task_type = result.task_type
        resp.routing_strategy = result.strategy
        # Fix finish_reason: "tool_calls" when tool_calls are present
        if resp.choices:
            msg = resp.choices[0].message
            has_tool_calls = bool(msg.tool_calls)
            resp.choices[0].finish_reason = "tool_calls" if has_tool_calls else "stop"
            if result.assistant_text and not has_tool_calls:
                msg.content = result.assistant_text
        return resp

    # Build from pipeline result (no raw_response — non-tool paths)
    resp = ChatCompletionResponse(
        id=result.response_id,
        model=result.model_name or inp.model,
        choices=[Choice(message=ChoiceMessage(content=result.assistant_text or ""))],
        usage=Usage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        ),
        provider=result.provider_name,
        task_type=result.task_type,
        routing_strategy=result.strategy,
        grounding_metadata=(
            __import__("json").loads(result.grounding_metadata)
            if result.grounding_metadata else None
        ),
    )

    # Persist non-streaming conversation (uses the request-scoped DB session)
    await _persist_conversation(
        inp=inp,
        messages=request.messages,
        assistant_text=result.assistant_text or "",
        result=result,
        api_key_hash=api_key_hash,
        source=source,
        tenant_id=tenant_id,
        db=db,
    )

    return resp


@router.get("/v1/models")
async def list_models(api_key: str = Depends(verify_api_key)):
    models = provider_registry.list_all_models()
    return {
        "object": "list",
        "data": [
            {
                "id": m.name,
                "object": "model",
                "owned_by": m.provider,
                "context_window": m.context_window,
            }
            for m in models
        ]
        + [
            {
                "id": "Atlas",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Atlas by Alpheric — default model",
            },
            {
                "id": "atlas-plan",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Planning, discussion, brainstorming",
            },
            {
                "id": "atlas-code",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Code generation, debugging, review",
            },
            {
                "id": "atlas-secure",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Security analysis, reasoning, auditing",
            },
            {
                "id": "atlas-infra",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Infrastructure, DevOps, deployment",
            },
            {
                "id": "atlas-data",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Data analysis, statistics, ETL",
            },
            {
                "id": "atlas-books",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Documentation, writing, research",
            },
            {
                "id": "atlas-audit",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Compliance auditing, log analysis, structured extraction",
            },
            {"id": "auto", "object": "model", "owned_by": "alpheric.ai", "context_window": 200000},
            {
                "id": "auto:fast",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
            },
            {
                "id": "auto:cheap",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
            },
            # alpheric-1 and local are functional routing aliases — not listed publicly
        ],
    }
