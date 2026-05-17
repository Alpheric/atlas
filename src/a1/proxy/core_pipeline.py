"""CorePipeline -- unified execution path for all Atlas request entry points.

All three routers (openai, atlas, responses) normalize their input format
into a CorePipelineInput, call CorePipeline.execute(), and format the
CorePipelineResult into their response format.

This eliminates duplication of: session load, PII mask, classification,
routing, distillation retry, PII unmask, session save, metrics, DB persist.
"""

import asyncio
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import Response

from a1.common.logging import get_logger
from a1.common.metrics import metrics
from a1.common.telemetry import record_otel_request
from a1.providers.registry import provider_registry
from a1.proxy.pipeline import (
    LEGACY_ALIASES,
    _load_session,
    _mask_pii,
    _persist_usage,
    execute_tool_loop,
    strip_think_tokens,
)
from a1.proxy.request_models import ChatCompletionRequest
from a1.routing.atlas_models import ATLAS_TASK_MAP, resolve_atlas_model
from a1.routing.classifier import classify_task, classify_task_with_fallback
from a1.routing.smart_router import RoutingDecision, smart_router
from a1.routing.strategy import select_model
from config.settings import settings

log = get_logger("proxy.pipeline")

# Context var for request ID (set by middleware, read by logging)
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# deepseek-r1 models need think-token stripping
_DEEPSEEK_R1_MODELS = frozenset(
    {"deepseek-r1:8b", "deepseek-r1:14b", "deepseek-r1:32b", "deepseek-r1:70b"}
)

# Task-repeat counter: tracks how many times each task_type has been handled
# in this server process lifetime. Used to trigger early local routing after
# settings.distillation_task_repeat_threshold requests without waiting for training.
_task_repeat_counts: dict[str, int] = {}


@dataclass
class CorePipelineInput:
    """Normalized request from any entry point."""

    # Identity
    request_id: str = ""
    source: str = "openai"  # "openai" | "atlas" | "responses"
    api_key_hash: str | None = None
    workspace_id: str | None = None
    # atlas_api_keys.source value resolved at the entry point ("onedesk",
    # "notifire", "proxy", …). Used by routing to pin specific tenants to
    # specific providers via settings.vertex_forced_sources.
    tenant_source: str | None = None

    # Messages (already normalized to MessageInput list)
    messages: list = field(default_factory=list)
    raw_user_input: str = ""  # last user turn text for session save

    # Model selection
    model: str = "auto"
    strategy: str = "best_quality"

    # Generation params
    temperature: float | None = None
    max_tokens: int = 1000
    stream: bool = False
    tools: list | None = None
    tool_choice: str | None = None
    # When True the caller handles the tool-execution loop (e.g. Hermes runs tools
    # locally).  Atlas makes ONE provider call, returns tool_use blocks directly to
    # the client, and does NOT run execute_tool_loop server-side.
    tool_passthrough: bool = False

    # Session
    session_id: str | None = None
    previous_response_id: str | None = None
    user_id: str | None = None

    # Source-specific flags
    skip_history_injection: bool = False  # OpenClaw sends full history
    use_llm_classifier: bool = False  # Atlas uses LLM fallback classifier
    atlas_model_override: str | None = None  # agent persona forced a model

    # Routing mode (quality | balanced | low_cost | local_first)
    routing_mode: str = "balanced"

    # DB context
    conversation_id: str | None = None


@dataclass
class CorePipelineResult:
    """Normalized result from pipeline execution."""

    response_id: str = ""
    assistant_text: str | None = None
    chunk_iterator: object | None = None  # async iterator for streaming

    # Routing
    provider_name: str = ""
    model_name: str = ""
    atlas_model: str | None = None
    task_type: str = "general"
    confidence: float = 0.0
    strategy: str = "best_quality"
    is_local: bool = False

    # Tokens
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    # Flags
    cache_hit: bool = False
    fast_path: bool = False
    distillation: bool = False
    pii_masked: bool = False
    session_id: str | None = None

    # Self-heal
    quality_score: float = 0.0
    self_healed: bool = False
    original_response: str | None = None  # pre-critique text kept for audit

    # Web search grounding
    web_search_run_id: str | None = None
    web_citations: list | None = None  # list[Citation] after response
    grounding_metadata: str | None = None  # JSON-serialised GroundingMetadata from Vertex

    # Smart routing observability
    shadow_model: str | None = None  # atlas-* model running in background
    routing_reason: str = ""  # why this model was selected
    routing_mode: str = "balanced"
    context_tokens: int = 0
    is_sticky: bool = False  # re-used session's primary model

    # Error
    error: str | None = None
    error_type: str | None = None  # "provider_error", "internal_error", etc.

    # Raw provider response (for openai compat format)
    raw_response: object | None = None


async def _tool_complete_and_stream(provider, req, timeout: float | None = None, fallback=None):
    """Run provider.complete() INSIDE the stream so SSE starts immediately.

    Emits the role chunk right away to keep Ares/Hermes alive during inference.
    If provider times out (agent_execution_timeout, default 1200s) and a fallback
    provider is given, retries once on the fallback before emitting an error chunk.
    """
    if timeout is None:
        timeout = float(settings.agent_execution_timeout)
    from a1.proxy.response_models import ChatCompletionChunk, DeltaMessage, StreamChoice

    chunk_id = f"chatcmpl-cli-{uuid.uuid4().hex[:8]}"
    model = req.model

    # Emit role chunk immediately — keeps the SSE connection alive while model runs
    yield ChatCompletionChunk(
        id=chunk_id,
        model=model,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )

    # Run the actual completion with a timeout; optionally retry on fallback
    resp = None
    for attempt, prov in enumerate([provider] + ([fallback] if fallback else [])):
        try:
            resp = await asyncio.wait_for(prov.complete(req), timeout=timeout)
            break
        except asyncio.TimeoutError:
            label = getattr(prov, "name", str(prov))
            log.warning(
                f"[tool_stream] {label} timed out after {timeout}s"
                + (" — retrying on fallback" if fallback and attempt == 0 else "")
            )
        except Exception as e:
            label = getattr(prov, "name", str(prov))
            log.error(
                f"[tool_stream] {label} error: {e}"
                + (" — retrying on fallback" if fallback and attempt == 0 else "")
            )

    if resp is None:
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[
                StreamChoice(
                    delta=DeltaMessage(content=f"Request timed out after {settings.agent_execution_timeout}s. The model took too long to respond — please try again."),
                    finish_reason="stop",
                )
            ],
        )
        return

    msg = resp.choices[0].message if resp.choices else None

    if msg and msg.tool_calls:
        for i, tc in enumerate(msg.tool_calls):
            yield ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            tool_calls=[
                                {
                                    "index": i,
                                    "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                                    "type": "function",
                                    "function": {"name": tc["function"]["name"], "arguments": ""},
                                }
                            ]
                        )
                    )
                ],
            )
            yield ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            tool_calls=[
                                {
                                    "index": i,
                                    "function": {"arguments": tc["function"]["arguments"]},
                                }
                            ]
                        )
                    )
                ],
            )
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason="tool_calls")],
        )
    else:
        content = (msg.content or "") if msg else ""
        if content:
            yield ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[StreamChoice(delta=DeltaMessage(content=content))],
            )
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
        )

    if resp.usage:
        from a1.proxy.response_models import Usage

        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[],
            usage=Usage(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                total_tokens=resp.usage.total_tokens,
            ),
        )


async def _tool_response_as_stream(resp):
    """Convert a ChatCompletionResponse with tool_calls into an OpenAI streaming iterator.

    Emits proper tool_calls delta chunks so that clients expecting streaming
    (e.g. Hermes/Ares) receive structured tool_calls instead of raw <tool_call> XML.
    """
    from a1.proxy.response_models import ChatCompletionChunk, DeltaMessage, StreamChoice

    chunk_id = f"chatcmpl-cli-{uuid.uuid4().hex[:8]}"
    model = resp.model
    msg = resp.choices[0].message if resp.choices else None

    # Role announcement
    yield ChatCompletionChunk(
        id=chunk_id,
        model=model,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )

    if msg and msg.tool_calls:
        for i, tc in enumerate(msg.tool_calls):
            # Header chunk: id + name
            yield ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            tool_calls=[
                                {
                                    "index": i,
                                    "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                                    "type": "function",
                                    "function": {"name": tc["function"]["name"], "arguments": ""},
                                }
                            ]
                        )
                    )
                ],
            )
            # Arguments chunk
            yield ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            tool_calls=[
                                {
                                    "index": i,
                                    "function": {"arguments": tc["function"]["arguments"]},
                                }
                            ]
                        )
                    )
                ],
            )
        # Finish with tool_calls reason
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason="tool_calls")],
        )
    elif msg and msg.content:
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(content=msg.content))],
        )
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
        )

    if resp.usage:
        from a1.proxy.response_models import Usage

        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[],
            usage=Usage(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                total_tokens=resp.usage.total_tokens,
            ),
        )


async def _text_response_as_stream(resp):
    """Simulate streaming for a buffered text response."""
    from a1.proxy.response_models import ChatCompletionChunk, DeltaMessage, StreamChoice

    chunk_id = f"chatcmpl-cli-{uuid.uuid4().hex[:8]}"
    model = resp.model
    msg = resp.choices[0].message if resp.choices else None
    text = (msg.content or "") if msg else ""

    yield ChatCompletionChunk(
        id=chunk_id,
        model=model,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )
    if text:
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(content=text))],
        )
    yield ChatCompletionChunk(
        id=chunk_id,
        model=model,
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    if resp.usage:
        from a1.proxy.response_models import Usage

        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[],
            usage=Usage(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                total_tokens=resp.usage.total_tokens,
            ),
        )


class CorePipeline:
    """Unified execution engine for all Atlas request flows."""

    async def execute(
        self,
        inp: CorePipelineInput,
        response: Response | None = None,
    ) -> CorePipelineResult:
        start_time = time.time()
        resp_id = inp.request_id or f"resp_{uuid.uuid4().hex[:12]}"

        result = CorePipelineResult(response_id=resp_id, strategy=inp.strategy)

        try:
            # Step 1: Resolve model aliases
            inp.model = LEGACY_ALIASES.get(inp.model, inp.model)
            if inp.atlas_model_override:
                inp.model = inp.atlas_model_override

            # Step 2: Session load (unless client sends full history)
            session = None
            if not inp.skip_history_injection:
                session, inp.messages = await self._load_session_safe(inp)
            # Stash session on inp so _classify_and_resolve can read sticky routing
            inp._session = session  # type: ignore[attr-defined]

            # Step 3: PII mask
            mask_map = {}
            if settings.pii_masking_enabled:
                inp.messages, mask_map = await asyncio.to_thread(_mask_pii, inp.messages)
                if mask_map:
                    # _mask_pii returns (messages, mask_map) where mask_map may be empty dict
                    inp.messages, mask_map = inp.messages, mask_map
                result.pii_masked = bool(mask_map)

            # Step 4: Classify task + resolve Atlas model (smart_router)
            task_type, confidence, atlas_model = await self._classify_and_resolve(inp)
            result.task_type = task_type
            result.confidence = confidence
            result.atlas_model = atlas_model

            # Propagate routing decision observability fields
            rd: RoutingDecision | None = getattr(inp, "routing_decision", None)
            if rd:
                result.routing_reason = rd.selection_reason
                result.routing_mode = rd.routing_mode
                result.context_tokens = rd.context_tokens
                result.is_sticky = rd.is_sticky
                result.shadow_model = rd.shadow_model
                log.info(
                    f"[route] task={task_type} model={rd.primary_model} "
                    f"provider={rd.primary_provider} mode={rd.routing_mode} "
                    f"reason={rd.selection_reason} sticky={rd.is_sticky} "
                    f"shadow={rd.shadow_model} ctx_tokens={rd.context_tokens}"
                )

            # Step 4b: Web search grounding — detect intent, search, inject context.
            # Runs before cache (search results must be part of the cached response)
            # and before budget (search adds a small cost tracked separately).
            # Safe for both streaming and non-streaming requests.
            search_ctx = None
            if settings.web_search_enabled:
                search_ctx = await self._maybe_search(inp, task_type, result)
                if search_ctx and not search_ctx.blocked:
                    if search_ctx.provider == "vertex_grounding":
                        # Vertex path: force routing to Vertex and enable googleSearch tool.
                        # The grounding happens inside the LLM call — no context block needed.
                        inp.force_provider = "vertex"  # type: ignore[attr-defined]
                        inp.metadata = getattr(inp, "metadata", {}) or {}
                        inp.metadata["web_search"] = True  # type: ignore[attr-defined]
                        result.web_search_run_id = search_ctx.run_id
                        log.info(
                            f"[search] Vertex grounding active — forcing provider=vertex "
                            f"intent={search_ctx.intent_score}"
                        )
                    else:
                        # External provider path: inject results as system message
                        from a1.search.pipeline import inject_search_context

                        inp.messages = inject_search_context(inp.messages, search_ctx)
                        result.web_search_run_id = search_ctx.run_id

            # Step 5: Cache check (non-streaming only)
            if not inp.stream and settings.task_cache_enabled and atlas_model:
                from a1.proxy.cache import task_cache

                cache_msgs = [{"role": m.role, "content": m.content or ""} for m in inp.messages]
                cached = task_cache.get(atlas_model, cache_msgs)
                if cached:
                    result.assistant_text = cached
                    result.cache_hit = True
                    result.provider_name = "cache"
                    result.latency_ms = int((time.time() - start_time) * 1000)
                    self._post_process(result, session, inp, mask_map, start_time)
                    return result

            # Step 5b: Budget check (if workspace has a budget)
            if inp.workspace_id:
                budget_ok = await self._check_budget(inp.workspace_id)
                if not budget_ok:
                    result.error = "Workspace monthly budget exceeded"
                    result.error_type = "rate_limit_error"
                    result.latency_ms = int((time.time() - start_time) * 1000)
                    return result

            # Step 5c: Task-repeat fast-routing — if the same task_type has been seen
            # >= distillation_task_repeat_threshold times since server start, AND a
            # healthy local Ollama provider exists, route to Ollama directly without
            # waiting for the full training pipeline to graduate it.
            # This "warms up" local routing quickly for repetitive agent tasks.
            threshold = settings.distillation_task_repeat_threshold
            # Skip fast-routing for tool requests — Ollama doesn't support our
            # <tool_call> prompt engineering format; Claude CLI must handle them.
            if threshold > 0 and task_type and not inp.stream and not inp.tools:
                _task_repeat_counts[task_type] = _task_repeat_counts.get(task_type, 0) + 1
                count = _task_repeat_counts[task_type]
                if count >= threshold:
                    ollama = provider_registry.get_provider("ollama")
                    if ollama and provider_registry.is_healthy("ollama"):
                        log.info(
                            f"[pipeline] Task-repeat fast-route: task={task_type} "
                            f"count={count}/{threshold} → ollama"
                        )
                        inp.model = "auto"  # let Ollama pick the best local model
                        await self._execute_local_only(inp, result, task_type)
                        if result.assistant_text:
                            result.latency_ms = int((time.time() - start_time) * 1000)
                            self._post_process(result, session, inp, mask_map, start_time)
                            return result
                        # If local failed, fall through to standard routing
            elif threshold > 0 and task_type and not inp.tools:
                _task_repeat_counts[task_type] = _task_repeat_counts.get(task_type, 0) + 1

            # Step 6: Route and execute
            await self._route_and_execute(inp, result, response, task_type, confidence, atlas_model)

            # Step 7: PII unmask
            if mask_map and result.assistant_text:
                from a1.security.pii_masker import pii_masker

                result.assistant_text = pii_masker.unmask(result.assistant_text, mask_map)

            # Step 7b: Finalise citations from web search (non-streaming only).
            # Re-scores which [N] markers appear in the response and persists
            # citation records to the DB.
            if search_ctx and result.assistant_text and not inp.stream:
                from a1.search.citation import build_citations, inject_citations_if_missing
                from a1.search.pipeline import persist_citations

                updated_citations = build_citations(
                    search_ctx.results,
                    result.assistant_text,
                    accessed_at=None,
                )
                # Append citation footer if the LLM didn't already include one
                result.assistant_text = inject_citations_if_missing(
                    result.assistant_text, updated_citations
                )
                result.web_citations = updated_citations
                asyncio.create_task(persist_citations(search_ctx.run_id, None, updated_citations))

            # Step 7c: Quality score + self-critique gate (non-streaming, external provider only)
            if result.assistant_text and not inp.stream and not result.cache_hit:
                from a1.healing.quality_scorer import score_response

                q_score = score_response(result.assistant_text, result.task_type)
                result.quality_score = q_score

                # Self-critique: replace response if score is below threshold
                if (
                    settings.self_critique_enabled
                    and not result.is_local  # only critique external provider responses
                    and q_score < settings.quality_min_score
                ):
                    from a1.healing.self_critique import self_critique

                    critique_provider = provider_registry.get_provider("claude-cli")
                    if critique_provider and provider_registry.is_healthy("claude-cli"):
                        log.info(
                            f"[self-heal] score={q_score:.3f} < {settings.quality_min_score}"
                            f" task={result.task_type} — triggering self-critique"
                        )
                        improved = await self_critique(
                            user_message=inp.raw_user_input or "",
                            original_response=result.assistant_text,
                            task_type=result.task_type,
                            provider=critique_provider,
                            model=settings.quality_critique_model,
                        )
                        if improved:
                            result.original_response = result.assistant_text
                            result.assistant_text = improved
                            result.self_healed = True
                            result.quality_score = score_response(improved, result.task_type)
                            log.info(
                                f"[self-heal] Response replaced "
                                f"(score: {q_score:.3f} → {result.quality_score:.3f})"
                            )

            # Step 8-12: Post-processing (cache store, session save, metrics, persist)
            result.latency_ms = int((time.time() - start_time) * 1000)
            self._post_process(result, session, inp, mask_map, start_time)

        except Exception as e:
            result.error = str(e)
            result.error_type = "internal_error"
            result.latency_ms = int((time.time() - start_time) * 1000)
            log.error(f"[pipeline] Execution error: {e}", exc_info=True)

        return result

    async def _maybe_search(
        self,
        inp: "CorePipelineInput",
        task_type: str | None,
        result: "CorePipelineResult",
    ):
        """Run web search pipeline if intent detected. Returns SearchContext or None."""
        try:
            from a1.search.pipeline import maybe_search

            ctx = await maybe_search(
                messages=inp.messages,
                task_type=task_type,
                workspace_id=inp.workspace_id,
                session_id=inp.session_id,
                atlas_model=result.atlas_model,
            )
            return ctx
        except Exception as e:
            log.warning(f"[pipeline] Web search failed (non-fatal): {e}")
            return None

    async def _check_budget(self, workspace_id: str) -> bool:
        """Check if workspace is within its monthly budget. Returns True if OK."""
        try:
            from sqlalchemy import select

            from a1.common.tz import now_ist
            from a1.db.engine import async_session
            from a1.db.models import WorkspaceBudget

            month = now_ist().strftime("%Y-%m")
            async with async_session() as session:
                result = await session.execute(
                    select(WorkspaceBudget).where(
                        WorkspaceBudget.workspace_id == uuid.UUID(workspace_id),
                        WorkspaceBudget.budget_month == month,
                    )
                )
                budget = result.scalar_one_or_none()
                if not budget:
                    return True  # no budget set = unlimited
                return float(budget.current_month_usd) < float(budget.monthly_limit_usd)
        except Exception as e:
            log.debug(f"Budget check failed (allowing request): {e}")
            return True  # fail open on budget check errors

    async def _load_session_safe(self, inp: CorePipelineInput):
        """Load session with grace timeout."""
        try:
            return await asyncio.wait_for(
                _load_session(
                    inp.session_id,
                    inp.previous_response_id,
                    inp.user_id,
                    inp.messages,
                ),
                timeout=settings.session_load_grace_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            log.warning(
                f"Session load exceeded {settings.session_load_grace_ms}ms, "
                "proceeding without history"
            )
            return None, inp.messages

    async def _execute_local_only(
        self, inp: CorePipelineInput, result: CorePipelineResult, task_type: str
    ) -> None:
        """Route directly to the best available local Ollama model.

        Used by the task-repeat fast-path after N identical task_type requests,
        and as a fallback when the external provider fails.
        """
        from a1.routing.strategy import select_model

        temp_req = ChatCompletionRequest(
            model="auto",
            messages=inp.messages,
            max_tokens=inp.max_tokens,
            temperature=inp.temperature,
            session_id=inp.session_id,
        )
        try:
            model_info = await select_model(
                task_type=task_type,
                confidence=1.0,
                request=temp_req,
                prefer_local=True,
            )
            provider = provider_registry.get_provider(model_info.provider)
            if provider is None or not provider_registry.is_healthy(model_info.provider):
                return
            resp = await provider.complete(temp_req)
            result.assistant_text = resp.choices[0].message.content
            result.provider_name = model_info.provider
            result.model_name = model_info.name
            result.is_local = True
            result.task_type = task_type
            result.prompt_tokens = resp.usage.prompt_tokens if resp.usage else 0
            result.completion_tokens = resp.usage.completion_tokens if resp.usage else 0
            result.total_tokens = resp.usage.total_tokens if resp.usage else 0
        except Exception as e:
            log.warning(f"[pipeline] local-only execution failed: {e}")

    async def _classify_and_resolve(self, inp: CorePipelineInput):
        """Classify task type and resolve Atlas model.

        Returns (task_type, confidence, atlas_model).
        Also populates inp.routing_decision (RoutingDecision) for the executor.
        """
        model = inp.model

        # ── Direct Atlas model specified (atlas-plan, atlas-code, etc.) ──
        if model in ATLAS_TASK_MAP:
            task_type = ATLAS_TASK_MAP[model]
            inp.routing_decision = smart_router.select(  # type: ignore[attr-defined]
                task_type=task_type,
                routing_mode=inp.routing_mode,
                context_tokens=self._estimate_context_tokens(inp),
                force_model=model,
            )
            inp.routing_decision.confidence = 1.0
            return task_type, 1.0, model

        # ── Public "Atlas" or auto/* aliases → classify and route ─────────
        is_atlas_public = (
            model in ("Atlas", "atlas", "alpheric-1", "local")
            or model.startswith("auto")
            or model.lower() == "atlas"
        )

        if is_atlas_public:
            temp_req = ChatCompletionRequest(
                model="auto",
                messages=inp.messages,
                max_tokens=inp.max_tokens,
            )
            if inp.use_llm_classifier:
                task_type, confidence = await classify_task_with_fallback(temp_req)
            else:
                task_type, confidence = classify_task(temp_req)

            # Adjust strategy → routing_mode mapping
            mode = inp.routing_mode
            if model == "auto:fast":
                mode = "low_cost"
            elif model == "auto:cheap":
                mode = "low_cost"

            # Get session sticky state
            session = getattr(inp, "_session", None)
            session_primary = getattr(session, "primary_model", None) if session else None
            session_task = getattr(session, "primary_task_type", None) if session else None

            ctx_tokens = self._estimate_context_tokens(inp)
            decision = smart_router.select(
                task_type=task_type,
                routing_mode=mode,
                context_tokens=ctx_tokens,
                session_primary_model=session_primary,
                session_primary_task=session_task,
            )
            decision.confidence = confidence
            inp.routing_decision = decision  # type: ignore[attr-defined]

            atlas_model = decision.atlas_model or resolve_atlas_model(task_type)
            return task_type, confidence, atlas_model

        # ── Explicit non-Atlas model (e.g. gemini-2.5-pro, qwen2.5-coder:7b) ──
        temp_req = ChatCompletionRequest(
            model=model,
            messages=inp.messages,
            max_tokens=inp.max_tokens,
        )
        task_type, confidence = classify_task(temp_req)
        # No atlas_model — direct provider routing
        decision = RoutingDecision(
            primary_model=model,
            task_type=task_type,
            routing_mode=inp.routing_mode,
            confidence=confidence,
            selection_reason="explicit_model",
        )
        smart_router._resolve_provider(decision)
        inp.routing_decision = decision  # type: ignore[attr-defined]
        return task_type, confidence, None

    def _estimate_context_tokens(self, inp: CorePipelineInput) -> int:
        """Estimate total token count for context-length routing decisions."""
        try:
            from a1.common.tokens import count_tokens

            full_text = " ".join(m.content or "" for m in inp.messages if hasattr(m, "content"))
            return count_tokens(full_text)
        except Exception:
            return 0

    async def _route_and_execute(
        self,
        inp: CorePipelineInput,
        result: CorePipelineResult,
        response: Response | None,
        task_type: str,
        confidence: float,
        atlas_model: str | None,
    ):
        """Route request to provider and execute."""
        # Forced-tenant override: tenants listed in A1_VERTEX_FORCED_SOURCES
        # always run on Vertex/Gemini regardless of normal routing. Used to
        # pin specific external services (e.g. notifire) to a known-cheap,
        # known-fast model. Falls through to normal routing if vertex is
        # unhealthy so the tenant still gets a response.
        forced_sources = settings.vertex_forced_sources or []
        if (
            inp.tenant_source
            and inp.tenant_source in forced_sources
            and not getattr(inp, "force_provider", None)
        ):
            vertex = provider_registry.get_provider("vertex")
            if vertex and provider_registry.is_healthy("vertex"):
                # Use the *forced-tenant* model, NOT vertex_default_model.
                # Keeps cheap-tenant pinning (e.g. gemini-2.0-flash) independent
                # of operator-tuned defaults for grounding/vision.
                model = settings.vertex_forced_sources_model or "gemini-2.0-flash"
                from a1.proxy.request_models import ChatCompletionRequest

                req = ChatCompletionRequest(
                    model=model,
                    messages=inp.messages,
                    max_tokens=inp.max_tokens,
                    temperature=inp.temperature,
                    stream=inp.stream,
                )
                result.provider_name = "vertex"
                result.model_name = model
                result.atlas_model = atlas_model  # keep dashboard attribution
                result.is_local = False
                result.strategy = "forced_tenant"
                result.routing_reason = f"forced-tenant:{inp.tenant_source}->{model}"
                log.info(
                    f"[pipeline] tenant_source={inp.tenant_source} → forcing vertex/{model}"
                )
                if inp.stream:
                    result.chunk_iterator = vertex.stream(req)
                    return
                resp = await vertex.complete(req)
                result.assistant_text = (
                    resp.choices[0].message.content if resp.choices else ""
                )
                result.prompt_tokens = resp.usage.prompt_tokens
                result.completion_tokens = resp.usage.completion_tokens
                result.cost_usd = vertex.estimate_cost(
                    resp.usage.prompt_tokens, resp.usage.completion_tokens, model
                )
                return
            log.warning(
                f"[pipeline] tenant_source={inp.tenant_source} should force vertex "
                "but vertex unhealthy — falling through to normal routing"
            )

        # Vision detection: if any message has image content, force Vertex (only vision provider).
        has_vision = any(
            getattr(m, "has_images", False) for m in inp.messages if not isinstance(m, dict)
        )
        if has_vision and not getattr(inp, "force_provider", None):
            inp.force_provider = "vertex"  # type: ignore[attr-defined]
            log.info("[pipeline] vision content detected → forcing provider=vertex")

        # Vertex grounding path: force straight to Vertex with web_search metadata.
        force_provider = getattr(inp, "force_provider", None)
        if force_provider == "vertex":
            vertex = provider_registry.get_provider("vertex")
            if vertex and provider_registry.is_healthy("vertex"):
                model = settings.vertex_default_model or "gemini-2.5-pro"
                from a1.proxy.request_models import ChatCompletionRequest

                req = ChatCompletionRequest(
                    model=model,
                    messages=inp.messages,
                    max_tokens=inp.max_tokens,
                    temperature=inp.temperature,
                    stream=inp.stream,
                )
                # Signal VertexProvider to enable googleSearch grounding
                req.metadata = {"web_search": True}  # type: ignore[attr-defined]
                result.provider_name = "vertex"
                result.model_name = model
                result.is_local = False
                if inp.stream:
                    result.chunk_iterator = vertex.stream(req)
                    return
                resp = await vertex.complete(req)
                result.assistant_text = resp.choices[0].message.content if resp.choices else ""
                result.prompt_tokens = resp.usage.prompt_tokens
                result.completion_tokens = resp.usage.completion_tokens
                result.cost_usd = vertex.estimate_cost(
                    resp.usage.prompt_tokens, resp.usage.completion_tokens, model
                )
                # Capture grounding metadata for citation storage
                gm = getattr(resp, "grounding_metadata", None)
                if gm:
                    import json as _json

                    result.grounding_metadata = _json.dumps(gm)  # type: ignore[attr-defined]
                return
            # Vertex unavailable — fall through to normal routing
            log.warning(
                "[pipeline] Vertex grounding requested but vertex unhealthy — falling through"
            )

        # Tool requests: bypass distillation & fast-path.
        # Priority: Vertex (Gemini native function calling) → claude-cli fallback.
        # Vertex has its own quota independent of Claude CLI accounts.
        if inp.tools and atlas_model:
            from a1.proxy.request_models import ChatCompletionRequest

            vertex = provider_registry.get_provider("vertex")
            cli = provider_registry.get_provider("claude-cli")
            vertex_ok = bool(vertex and provider_registry.is_healthy("vertex"))
            cli_ok = bool(cli and provider_registry.is_healthy("claude-cli"))

            if vertex_ok:
                _vdm = settings.vertex_default_model
                vertex_model = str(_vdm) if isinstance(_vdm, str) else "gemini-2.5-flash"
                req = ChatCompletionRequest(
                    model=vertex_model,
                    messages=inp.messages,
                    max_tokens=inp.max_tokens,
                    temperature=inp.temperature,
                    stream=False,
                    tools=inp.tools,
                    tool_choice=inp.tool_choice,
                )
                log.info(f"[pipeline] tool request → vertex/{vertex_model}")

                if inp.stream:
                    # Vertex (LiteLLM) supports streaming tool_calls natively.
                    # Use real streaming so users see tokens as they arrive
                    # instead of waiting up to 1200s for the full response
                    # to buffer. The fallback to claude-cli only triggers
                    # if vertex.stream() raises before yielding anything;
                    # mid-stream failures will surface as a short response.
                    req_stream = req.model_copy(update={"stream": True})

                    async def _vertex_stream_with_fallback():
                        try:
                            async for chunk in vertex.stream(req_stream):
                                yield chunk
                            return
                        except Exception as e:
                            log.warning(
                                f"[pipeline] vertex stream failed early ({e})"
                                + (" — falling back to claude-cli buffered path" if cli_ok else "")
                            )
                            if not cli_ok:
                                raise
                        # Fallback path: buffered claude-cli
                        async for chunk in _tool_complete_and_stream(
                            cli, req, timeout=float(settings.agent_execution_timeout)
                        ):
                            yield chunk

                    result.chunk_iterator = _vertex_stream_with_fallback()
                    result.provider_name = "vertex"
                    result.model_name = vertex_model
                    return

                resp = None
                used_provider = "vertex"
                try:
                    resp = await asyncio.wait_for(
                        vertex.complete(req), timeout=float(settings.agent_execution_timeout)
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    log.warning(
                        f"[pipeline] vertex tool call failed ({e}) — falling back to claude-cli"
                    )
                    if cli_ok:
                        from a1.training.auto_trainer import _get_external_provider

                        _, _, ext_model = _get_external_provider(atlas_model)
                        cli_req = ChatCompletionRequest(
                            model=ext_model or atlas_model,
                            messages=inp.messages,
                            max_tokens=inp.max_tokens,
                            temperature=inp.temperature,
                            stream=False,
                            tools=inp.tools,
                            tool_choice=inp.tool_choice,
                        )
                        resp = await cli.complete(cli_req)
                        used_provider = "claude-cli"

                if resp:
                    result.raw_response = resp
                    result.provider_name = used_provider
                    result.model_name = vertex_model if used_provider == "vertex" else atlas_model
                    result.prompt_tokens = resp.usage.prompt_tokens if resp.usage else 0
                    result.completion_tokens = resp.usage.completion_tokens if resp.usage else 0
                    result.total_tokens = resp.usage.total_tokens if resp.usage else 0
                    msg = resp.choices[0].message if resp.choices else None
                    result.assistant_text = (msg.content or "") if msg else ""
                    return

            # ── Vertex unavailable — go directly to claude-cli ───────────────────
            if cli_ok:
                from a1.training.auto_trainer import _get_external_provider

                _, _, ext_model = _get_external_provider(atlas_model)
                req = ChatCompletionRequest(
                    model=ext_model or atlas_model,
                    messages=inp.messages,
                    max_tokens=inp.max_tokens,
                    temperature=inp.temperature,
                    stream=False,
                    tools=inp.tools,
                    tool_choice=inp.tool_choice,
                )
                log.info(
                    f"[pipeline] tool request → claude-cli/{ext_model or atlas_model}"
                    " (vertex unavailable)"
                )

                if inp.stream:
                    result.chunk_iterator = _tool_complete_and_stream(
                        cli, req, timeout=float(settings.agent_execution_timeout)
                    )
                    result.provider_name = "claude-cli"
                    result.model_name = atlas_model
                    return

                resp = await cli.complete(req)
                result.raw_response = resp
                result.provider_name = "claude-cli"
                result.model_name = atlas_model
                result.prompt_tokens = resp.usage.prompt_tokens if resp.usage else 0
                result.completion_tokens = resp.usage.completion_tokens if resp.usage else 0
                result.total_tokens = resp.usage.total_tokens if resp.usage else 0
                msg = resp.choices[0].message if resp.choices else None
                result.assistant_text = (msg.content or "") if msg else ""
                return

        # Atlas distillation path — skip when tools are present (handled above).
        if settings.distillation_enabled and atlas_model and not inp.tools:
            await self._distillation_path(inp, result, response, task_type, confidence, atlas_model)
            if result.assistant_text or result.chunk_iterator:
                return

        # Direct provider path (non-Atlas or distillation failed)
        await self._direct_provider_path(inp, result, task_type)

    async def _distillation_path(
        self,
        inp,
        result,
        response,
        task_type,
        confidence,
        atlas_model,
    ):
        """Execute via distillation (teacher+student) with retry."""
        from a1.training.auto_trainer import (
            _get_external_provider,
            handle_dual_execution,
            handle_dual_execution_stream,
        )

        resp_obj = response or Response()
        temp_req = ChatCompletionRequest(
            model="auto",
            messages=inp.messages,
            max_tokens=inp.max_tokens,
            temperature=inp.temperature,
            session_id=inp.session_id,
        )

        _, ext_name, _ = _get_external_provider(atlas_model)
        ext_name = ext_name or "external"

        # Streaming distillation
        if inp.stream:
            chunk_iter = await handle_dual_execution_stream(
                temp_req,
                task_type,
                confidence,
                atlas_model=atlas_model,
            )
            if chunk_iter is None:
                log.warning(f"[pipeline] Stream distillation failed for {atlas_model}, retrying")
                chunk_iter = await handle_dual_execution_stream(
                    temp_req,
                    task_type,
                    confidence,
                    atlas_model=atlas_model,
                )
            if chunk_iter is not None:
                result.chunk_iterator = chunk_iter
                result.provider_name = ext_name
                result.model_name = atlas_model
                result.atlas_model = atlas_model
                result.distillation = True
                return

        # Non-streaming distillation (retry once)
        dual = await handle_dual_execution(
            temp_req,
            resp_obj,
            task_type,
            confidence,
            atlas_model=atlas_model,
        )
        if dual is None:
            log.warning(f"[pipeline] Distillation failed for {atlas_model}, retrying")
            dual = await handle_dual_execution(
                temp_req,
                resp_obj,
                task_type,
                confidence,
                atlas_model=atlas_model,
            )

        if dual is not None and dual.choices:
            result.assistant_text = dual.choices[0].message.content or ""
            result.provider_name = getattr(dual, "provider", ext_name) or ext_name
            result.model_name = atlas_model
            result.atlas_model = atlas_model
            result.distillation = True
            result.prompt_tokens = dual.usage.prompt_tokens
            result.completion_tokens = dual.usage.completion_tokens
            result.total_tokens = dual.usage.total_tokens
            result.raw_response = dual

            # Cost estimation
            p = provider_registry.get_provider(result.provider_name)
            if p:
                result.cost_usd = p.estimate_cost(
                    dual.usage.prompt_tokens,
                    dual.usage.completion_tokens,
                    getattr(dual, "model", "") or atlas_model,
                )

    async def _direct_provider_path(self, inp, result, task_type):
        """Route to a specific provider directly (local or external).

        Uses RoutingDecision from smart_router when available, falls back to
        legacy select_model() for backward compatibility.
        """
        model = inp.model
        rd: RoutingDecision | None = getattr(inp, "routing_decision", None)

        # Smart router provided a decision — use its non-atlas model directly
        if (
            rd
            and rd.provider_model
            and not (rd.atlas_model and rd.primary_provider == "claude-cli")
        ):
            model_name = rd.provider_model
            provider_name = rd.primary_provider
            # Try fallback chain if primary provider unhealthy
            if not provider_registry.is_healthy(provider_name):
                for fb_model in rd.fallback_models:
                    p = provider_registry.get_provider_for_model(fb_model)
                    if p and provider_registry.is_healthy(p.name):
                        model_name = fb_model
                        provider_name = p.name
                        log.info(f"[route] Fallback: {rd.primary_model} → {fb_model} ({p.name})")
                        result.routing_reason = f"fallback:{rd.primary_model}->{fb_model}"
                        break
        elif (
            model.startswith("auto")
            or model == "local"
            or model in ("Atlas", "atlas", "alpheric-1")
        ):
            strategy = inp.strategy
            if model == "auto:fast":
                strategy = "lowest_latency"
            elif model == "auto:cheap":
                strategy = "lowest_cost"
            model_name, provider_name = await select_model(task_type, strategy)
        else:
            model_name = model
            p = provider_registry.get_provider_for_model(model)
            provider_name = p.name if p else "unknown"

        provider = provider_registry.get_provider(provider_name)
        if not provider:
            # Fallback: any healthy provider
            for name, p in provider_registry.healthy_providers.items():
                provider = p
                models = p.list_models()
                if models:
                    model_name = models[0].name
                    provider_name = name
                break

        if not provider:
            result.error = f"No provider available for model: {model}"
            result.error_type = "provider_error"
            return

        req = ChatCompletionRequest(
            model=model_name,
            messages=inp.messages,
            max_tokens=inp.max_tokens,
            temperature=inp.temperature,
            stream=inp.stream,
            tools=inp.tools,
            tool_choice=inp.tool_choice,
        )

        is_local = provider_name == "ollama"
        result.is_local = is_local
        result.provider_name = provider_name
        result.model_name = model_name

        try:
            if inp.stream:
                result.chunk_iterator = provider.stream(req)
                return

            # Long-context chunking — MapReduce when content exceeds provider window.
            # Only for non-streaming, non-tool-use text requests.
            if not req.tools and not inp.tool_passthrough:
                from a1.chunking.chunker import chunk_and_reduce, needs_chunking
                from a1.common.tokens import count_tokens

                ctx_tokens = sum(
                    count_tokens(m.content or "") for m in inp.messages if hasattr(m, "content")
                )
                provider_models = provider.list_models() if hasattr(provider, "list_models") else []
                ctx_window = next(
                    (m.context_window for m in provider_models if m.name == model_name),
                    128_000,
                )
                if needs_chunking(ctx_tokens, ctx_window):
                    log.info(
                        f"[pipeline] Long-context chunking: {ctx_tokens}tok > "
                        f"{int(ctx_window * 0.85)}tok threshold (window={ctx_window})"
                    )
                    text = await chunk_and_reduce(provider, req, ctx_window)
                    result.assistant_text = text
                    result.prompt_tokens = ctx_tokens
                    result.completion_tokens = count_tokens(text)
                    result.total_tokens = result.prompt_tokens + result.completion_tokens
                    result.routing_reason = (result.routing_reason or "") + "+chunked"
                    if not is_local:
                        result.cost_usd = provider.estimate_cost(
                            result.prompt_tokens, result.completion_tokens, model_name
                        )
                    return

            if req.tools and not inp.tool_passthrough:
                # Atlas executes its own server-side tools (agent / planning mode)
                resp = await execute_tool_loop(provider, req)
            else:
                # tool_passthrough=True → client runs the tool loop (e.g. Hermes);
                # or no tools at all → single completion call.
                resp = await provider.complete(req)

            text = resp.choices[0].message.content if resp.choices else ""
            if model_name in _DEEPSEEK_R1_MODELS:
                text = strip_think_tokens(text)

            result.assistant_text = text
            result.prompt_tokens = resp.usage.prompt_tokens
            result.completion_tokens = resp.usage.completion_tokens
            result.total_tokens = resp.usage.total_tokens
            result.raw_response = resp

            if not is_local:
                result.cost_usd = provider.estimate_cost(
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                    model_name,
                )

        except Exception as e:
            log.error(f"[pipeline] Provider {provider_name}/{model_name} error: {e}")
            # Attempt OpenAI cascade before declaring failure.
            # Only for non-streaming non-tool requests (streaming errors surface
            # to the client during iteration; tool requests are handled above).
            if not inp.stream and not inp.tools and provider_name != "openai":
                cascaded = await self._try_openai_cascade(inp, result, task_type, str(e))
                if not cascaded:
                    result.error = str(e)
                    result.error_type = "provider_error"
            else:
                result.error = str(e)
                result.error_type = "provider_error"

    async def _try_openai_cascade(
        self,
        inp: "CorePipelineInput",
        result: "CorePipelineResult",
        task_type: str,
        original_error: str,
    ) -> bool:
        """Cascade to OpenAI when the primary provider fails (non-streaming, non-tool).

        Controlled by settings.agent_builder_openai_fallback.  Returns True if the
        fallback produced a response, False otherwise.
        """
        if not settings.agent_builder_openai_fallback:
            return False

        openai_provider = provider_registry.get_provider("openai")
        if not openai_provider or not provider_registry.is_healthy("openai"):
            log.debug("[pipeline] OpenAI cascade: provider not healthy — skipping")
            return False

        fallback_model = "o3-mini" if task_type in ("reasoning", "math") else "gpt-4o"
        log.warning(
            f"[pipeline] Primary provider failed ({original_error[:120]}) — "
            f"cascading to openai/{fallback_model}"
        )

        try:
            fb_req = ChatCompletionRequest(
                model=fallback_model,
                messages=inp.messages,
                max_tokens=inp.max_tokens,
                temperature=inp.temperature,
            )
            resp = await asyncio.wait_for(openai_provider.complete(fb_req), timeout=45.0)
            if not resp.choices:
                return False

            result.assistant_text = resp.choices[0].message.content or ""
            result.provider_name = "openai"
            result.model_name = fallback_model
            result.is_local = False
            result.prompt_tokens = resp.usage.prompt_tokens if resp.usage else 0
            result.completion_tokens = resp.usage.completion_tokens if resp.usage else 0
            result.total_tokens = resp.usage.total_tokens if resp.usage else 0
            result.cost_usd = openai_provider.estimate_cost(
                result.prompt_tokens, result.completion_tokens, fallback_model
            )
            result.routing_reason = (result.routing_reason or "") + "+openai_cascade"
            result.raw_response = resp
            log.info(
                f"[pipeline] OpenAI cascade succeeded: "
                f"{result.completion_tokens} completion tokens via {fallback_model}"
            )
            return True

        except Exception as fb_err:
            log.error(f"[pipeline] OpenAI cascade also failed: {fb_err}")
            return False

    def _post_process(
        self,
        result: CorePipelineResult,
        session,
        inp: CorePipelineInput,
        mask_map: dict,
        start_time: float,
    ):
        """Steps 8-12: cache store, session save, metrics, DB persist."""
        # Step 8: Cache store
        # Guard: never cache error responses from the CLI or provider — they look like
        # valid text ("Not logged in", "Claude CLI exit code 1", etc.) but are poison.
        _CACHE_POISON_SIGNALS = (
            "not logged in",
            "authentication",
            "cli exit code",
            "no healthy",
            "timed out after",
            "rate limit",
            "overloaded",
            "internal server error",
        )
        _text_lower = (result.assistant_text or "").lower()
        _is_poisoned = any(s in _text_lower for s in _CACHE_POISON_SIGNALS)

        if (
            not inp.stream
            and not result.error
            and not _is_poisoned
            and result.assistant_text
            and settings.task_cache_enabled
            and result.atlas_model
        ):
            from a1.proxy.cache import task_cache

            cache_msgs = [{"role": m.role, "content": m.content or ""} for m in inp.messages]
            task_cache.put(result.atlas_model, cache_msgs, result.assistant_text, result.task_type)

        # Step 9: Session save + routing stickiness update
        if session and result.assistant_text:
            session.add_message("user", inp.raw_user_input or "")
            session.add_message("assistant", result.assistant_text or "")
            # Update session's sticky routing model (first successful response wins)
            if result.provider_name and result.model_name and not result.cache_hit:
                sticky_model = result.atlas_model or result.model_name
                session.set_routing(
                    model=sticky_model,
                    provider=result.provider_name,
                    task_type=result.task_type,
                )
                # Propagate routing_mode from inp so it sticks
                session.routing_mode = inp.routing_mode
            from a1.session.manager import session_manager

            asyncio.create_task(session_manager.link_response(result.response_id, session.id))
            result.session_id = session.id

        # Step 10: Metrics
        if not result.cache_hit:
            metrics.record_request(
                result.provider_name,
                result.model_name or inp.model,
                result.task_type,
                result.latency_ms,
                result.cost_usd,
                result.prompt_tokens,
                result.completion_tokens,
                is_local=result.is_local,
            )
            record_otel_request(
                result.provider_name,
                result.model_name or inp.model,
                result.task_type,
                result.latency_ms,
                result.cost_usd,
                result.prompt_tokens,
                result.completion_tokens,
            )

        # Step 11: Background usage persist
        asyncio.create_task(
            _persist_usage(
                result.provider_name or "unknown",
                result.model_name or inp.model,
                result.is_local,
                result.prompt_tokens,
                result.completion_tokens,
                result.cost_usd,
                result.latency_ms,
                inp.api_key_hash,
            )
        )


# Singleton
core_pipeline = CorePipeline()
