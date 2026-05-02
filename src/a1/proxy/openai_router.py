"""OpenAI-compatible endpoints: /v1/chat/completions and /v1/models.

Thin adapter that normalizes ChatCompletionRequest into CorePipelineInput,
delegates to CorePipeline.execute(), and formats the result as
ChatCompletionResponse.
"""

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


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    response: Response,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    api_key_hash = hash_key(api_key) if api_key != "dev" else None
    rid = request_id_var.get("")

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
        tools=request.tools,
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

    # Streaming: return SSE
    if result.chunk_iterator:
        from a1.common.tokens import count_messages_tokens_for_model, count_tokens_for_model

        messages_dicts = [m.model_dump(exclude_none=True) for m in request.messages]
        model_name = result.model_name or inp.model

        async def stream_and_log():
            full_content = ""
            stream_usage = None

            async for chunk in result.chunk_iterator:
                if chunk.choices and chunk.choices[0].delta.content:
                    full_content += chunk.choices[0].delta.content
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

        return await sse_stream(stream_and_log())

    # Non-streaming: format as ChatCompletionResponse
    if result.raw_response and isinstance(result.raw_response, ChatCompletionResponse):
        # Distillation path returns a ChatCompletionResponse directly
        resp = result.raw_response
        resp.provider = result.provider_name
        resp.task_type = result.task_type
        resp.routing_strategy = result.strategy
        # Ensure text is PII-unmasked (pipeline does this)
        if resp.choices and result.assistant_text:
            resp.choices[0].message.content = result.assistant_text
        return resp

    # Build from pipeline result
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
    )

    # Persist conversation to DB (background-safe)
    try:
        conv_repo = ConversationRepo(db)
        msg_repo = MessageRepo(db)
        routing_repo = RoutingRepo(db)

        conv_id = uuid.UUID(inp.conversation_id) if inp.conversation_id else None
        if not conv_id:
            conv = await conv_repo.create(source="proxy", user_id=inp.user_id)
            conv_id = conv.id

        seq = 0
        for m in request.messages:
            await msg_repo.add(conv_id, m.role, m.content or "", seq)
            seq += 1
        assistant_msg = await msg_repo.add(conv_id, "assistant", result.assistant_text or "", seq)

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
        # Fire-and-forget quality signal persist
        if not result.cache_hit and result.quality_score > 0:
            import asyncio as _asyncio
            from a1.healing.quality_scorer import score_and_store as _score_and_store
            _asyncio.create_task(
                _score_and_store(result.assistant_text or "", result.task_type, str(assistant_msg.id))
            )
    except Exception as e:
        log.error(f"Failed to persist conversation: {e}")

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
            {
                "id": "alpheric-1",
                "object": "model",
                "owned_by": "alpheric.ai",
                "context_window": 200000,
                "description": "Legacy alias for atlas-plan",
            },
            {"id": "local", "object": "model", "owned_by": "alpheric.ai", "context_window": 4096},
        ],
    }
