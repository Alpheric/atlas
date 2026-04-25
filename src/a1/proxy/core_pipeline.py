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

    # Session
    session_id: str | None = None
    previous_response_id: str | None = None
    user_id: str | None = None

    # Source-specific flags
    skip_history_injection: bool = False  # OpenClaw sends full history
    use_llm_classifier: bool = False  # Atlas uses LLM fallback classifier
    atlas_model_override: str | None = None  # agent persona forced a model

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

    # Error
    error: str | None = None
    error_type: str | None = None  # "provider_error", "internal_error", etc.

    # Raw provider response (for openai compat format)
    raw_response: object | None = None


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

            # Step 3: PII mask
            mask_map = {}
            if settings.pii_masking_enabled:
                inp.messages, mask_map = await asyncio.to_thread(_mask_pii, inp.messages)
                if mask_map:
                    # _mask_pii returns (messages, mask_map) where mask_map may be empty dict
                    inp.messages, mask_map = inp.messages, mask_map
                result.pii_masked = bool(mask_map)

            # Step 4: Classify task + resolve Atlas model
            task_type, confidence, atlas_model = await self._classify_and_resolve(inp)
            result.task_type = task_type
            result.confidence = confidence
            result.atlas_model = atlas_model

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
            if threshold > 0 and task_type and not inp.stream:
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
            elif threshold > 0 and task_type:
                _task_repeat_counts[task_type] = _task_repeat_counts.get(task_type, 0) + 1

            # Step 6: Route and execute
            await self._route_and_execute(inp, result, response, task_type, confidence, atlas_model)

            # Step 7: PII unmask
            if mask_map and result.assistant_text:
                from a1.security.pii_masker import pii_masker

                result.assistant_text = pii_masker.unmask(result.assistant_text, mask_map)

            # Step 8-12: Post-processing (cache store, session save, metrics, persist)
            result.latency_ms = int((time.time() - start_time) * 1000)
            self._post_process(result, session, inp, mask_map, start_time)

        except Exception as e:
            result.error = str(e)
            result.error_type = "internal_error"
            result.latency_ms = int((time.time() - start_time) * 1000)
            log.error(f"[pipeline] Execution error: {e}", exc_info=True)

        return result

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
        """Classify task type and resolve Atlas model."""
        model = inp.model
        task_type = None
        confidence = 0.0
        atlas_model = None

        # Direct Atlas model specified
        if model in ATLAS_TASK_MAP:
            task_type = ATLAS_TASK_MAP[model]
            confidence = 1.0
            atlas_model = model
            return task_type, confidence, atlas_model

        # Auto-select: classify and resolve
        if model == "atlas" or model.startswith("auto") or model == "local":
            temp_req = ChatCompletionRequest(
                model="auto",
                messages=inp.messages,
                max_tokens=inp.max_tokens,
            )
            if inp.use_llm_classifier:
                task_type, confidence = await classify_task_with_fallback(temp_req)
            else:
                task_type, confidence = classify_task(temp_req)

            if model == "atlas" or model in ATLAS_TASK_MAP:
                atlas_model = resolve_atlas_model(task_type)
            return task_type, confidence, atlas_model

        # Explicit non-Atlas model
        temp_req = ChatCompletionRequest(
            model=model,
            messages=inp.messages,
            max_tokens=inp.max_tokens,
        )
        task_type, confidence = classify_task(temp_req)
        return task_type, confidence, None

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
        # Atlas distillation path
        if settings.distillation_enabled and atlas_model:
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
        """Route to a specific provider directly (local or external)."""
        model = inp.model
        strategy = inp.strategy

        if model.startswith("auto") or model == "local":
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

            if req.tools:
                resp = await execute_tool_loop(provider, req)
            else:
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
            result.error = str(e)
            result.error_type = "provider_error"
            log.error(f"[pipeline] Provider {provider_name}/{model_name} error: {e}")

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
        if (
            not inp.stream
            and not result.error
            and result.assistant_text
            and settings.task_cache_enabled
            and result.atlas_model
        ):
            from a1.proxy.cache import task_cache

            cache_msgs = [{"role": m.role, "content": m.content or ""} for m in inp.messages]
            task_cache.put(result.atlas_model, cache_msgs, result.assistant_text, result.task_type)

        # Step 9: Session save
        if session and result.assistant_text:
            session.add_message("user", inp.raw_user_input or "")
            session.add_message("assistant", result.assistant_text or "")
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
