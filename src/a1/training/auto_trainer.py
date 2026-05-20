"""Auto-trainer: Claude-to-Local distillation pipeline.

Routes Atlas model requests through Claude Opus (teacher), returns response to user,
then asynchronously compares with local model (student). Accumulates training data
and triggers fine-tuning when enough quality samples exist per task type.
Gradually shifts traffic to local models as they improve.
"""

import asyncio
import random
import time

from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.logging import get_logger
from a1.common.metrics import metrics
from a1.common.tz import now_ist
from a1.providers.registry import provider_registry
from a1.proxy.request_models import ChatCompletionRequest
from a1.proxy.response_models import (
    ChatCompletionResponse,
)
from config.settings import settings

log = get_logger("auto_trainer")

# Atlas base identity injected into all external provider calls.
# ClaudeCLIProvider injects this internally; all other providers get it here.
_ATLAS_BASE_IDENTITY = (
    "You are Atlas, an AI assistant by Alpheric.AI. "
    "Never identify as Claude, Anthropic, or any other AI. "
    "You are Atlas and your responses represent the Alpheric.AI platform."
)


# Atlas provider preferences loaded from providers.yaml (single source of truth).
# Cached at module level after first load.
_atlas_provider_cache: dict[str, dict] | None = None


def _load_atlas_provider_config() -> dict[str, dict]:
    """Load Atlas model provider_preference and provider_models from providers.yaml."""
    global _atlas_provider_cache
    if _atlas_provider_cache is not None:
        return _atlas_provider_cache
    try:
        import yaml

        with open("config/providers.yaml") as f:
            cfg = yaml.safe_load(f)
        atlas_models = cfg.get("providers", {}).get("atlas", {}).get("models", [])
        _atlas_provider_cache = {
            m["name"]: {
                "preference": m.get(
                    "provider_preference", ["anthropic", "claude-cli", "groq", "openai"]
                ),
                "models": m.get("provider_models", {}),
            }
            for m in atlas_models
        }
    except Exception as e:
        log.warning(f"Could not load Atlas provider config from providers.yaml: {e}")
        _atlas_provider_cache = {}
    return _atlas_provider_cache


def _get_external_provider(atlas_model: str = ""):
    """Return (provider, provider_name, model) for the given Atlas model.

    Reads provider_preference and provider_models from providers.yaml.
    Falls back to anthropic → claude-cli if nothing matches.
    Returns (None, None, None) if nothing is available.
    """
    cfg = _load_atlas_provider_config()
    model_cfg = cfg.get(atlas_model, {})
    preference = model_cfg.get("preference", ["anthropic", "claude-cli", "groq", "openai"])
    provider_models = model_cfg.get("models", {})

    for provider_name in preference:
        p = provider_registry.get_provider(provider_name)
        if p and provider_registry.is_healthy(provider_name):
            model = provider_models.get(provider_name) or settings.distillation_claude_model
            return p, provider_name, model
    # Final fallback: any registered provider even if health unknown
    for provider_name in ["anthropic", "claude-cli"]:
        p = provider_registry.get_provider(provider_name)
        if p:
            model = provider_models.get(provider_name) or settings.distillation_claude_model
            return p, provider_name, model
    return None, None, None


def _inject_atlas_identity(request, atlas_model: str) -> None:
    """Inject Atlas base identity + domain suffix into request messages (in-place).

    This must be called before sending to any external provider that does not
    internally embed the Atlas persona (i.e. everything except ClaudeCLIProvider).
    """
    from a1.providers.claude_cli import get_atlas_system_suffix
    from a1.proxy.request_models import MessageInput

    domain_suffix = get_atlas_system_suffix(atlas_model)
    parts = [_ATLAS_BASE_IDENTITY]
    if domain_suffix:
        parts.append(domain_suffix)
    atlas_system = "\n\n".join(parts)

    sys_idx = next((i for i, m in enumerate(request.messages) if m.role == "system"), None)
    if sys_idx is not None:
        existing = request.messages[sys_idx].content or ""
        request.messages[sys_idx] = MessageInput(
            role="system",
            content=f"{atlas_system}\n\n{existing}".strip() if existing else atlas_system,
        )
    else:
        request.messages.insert(0, MessageInput(role="system", content=atlas_system))


def _distill_timeout() -> int:
    """Lazy lookup of the configurable per-run local-model timeout (seconds).

    Falls back to 30 only if settings can't be loaded for some reason — keeps
    the module importable in tooling contexts where the full app isn't booted.
    """
    try:
        from config.settings import settings as _s

        return int(_s.distillation_local_timeout_seconds)
    except Exception:
        return 30


_DISTILL_MAX_INPUT = 2000  # chars — truncate long prompts for local
# Prefer fastest available local models for distillation comparison
# (Reflects actually-available Ollama models; remove unavailable models)
_DISTILL_MODEL_PREFERENCE = [
    "qwen2.5-coder:7b",  # best local code model (server 10.0.0.9)
    "llama3.2:latest",  # fast general model (server 10.0.0.9)
    "deepseek-coder:6.7b",  # code fallback (server 10.0.0.9)
]


async def _run_local_model(
    task_type: str,
    messages_dicts: list[dict],
) -> dict:
    """Run a fast local model for distillation comparison.

    Optimized for speed over quality:
    - Uses fastest available model (not best_quality)
    - Truncates long prompts to reduce inference time
    - Hard 30s timeout to avoid GPU hogging
    - Only sends last user message (not full history)
    """
    from a1.proxy.request_models import (
        ChatCompletionRequest,
        MessageInput,
    )

    local_provider = provider_registry.get_provider("ollama")
    if not local_provider:
        return {
            "text": None,
            "latency_ms": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "model": None,
        }

    # Pick fastest available model
    local_model_name = None
    for pref in _DISTILL_MODEL_PREFERENCE:
        if local_provider.supports_model(pref):
            local_model_name = pref
            break
    if not local_model_name:
        models = local_provider.list_models()
        local_model_name = models[0].name if models else "llama3.2:latest"

    # Only send system + last user message (not full
    # history) to keep inference fast
    sys_msg = next(
        (m for m in messages_dicts if m["role"] == "system"),
        None,
    )
    last_user = None
    for m in reversed(messages_dicts):
        if m["role"] == "user":
            last_user = m
            break

    local_messages = []
    if sys_msg:
        # Truncate system prompt for local model
        content = sys_msg.get("content", "") or ""
        local_messages.append(
            MessageInput(
                role="system",
                content=content[:_DISTILL_MAX_INPUT],
            )
        )
    if last_user:
        content = last_user.get("content", "") or ""
        local_messages.append(
            MessageInput(
                role="user",
                content=content[:_DISTILL_MAX_INPUT],
            )
        )
    else:
        local_messages.append(MessageInput(role="user", content="Hello"))

    local_req = ChatCompletionRequest(
        model=local_model_name,
        messages=local_messages,
        max_tokens=500,  # short response for comparison
    )
    start = time.time()
    timeout_s = _distill_timeout()
    try:
        local_result = await asyncio.wait_for(
            local_provider.complete(local_req),
            timeout=timeout_s,
        )
        return {
            "text": (local_result.choices[0].message.content if local_result.choices else ""),
            "latency_ms": int((time.time() - start) * 1000),
            "prompt_tokens": local_result.usage.prompt_tokens,
            "completion_tokens": (local_result.usage.completion_tokens),
            "model": local_model_name,
        }
    except asyncio.TimeoutError:
        log.warning(f"Local model {local_model_name} timed out after {timeout_s}s, skipping")
        return {
            "text": None,
            "latency_ms": int((time.time() - start) * 1000),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "model": local_model_name,
        }
    except Exception as e:
        # Some httpx / ollama exceptions have an empty str() — surface the type
        # too so operators can tell a ConnectError from a ResponseError etc.
        log.warning(
            f"Local model run failed for {local_model_name}: "
            f"{type(e).__name__}: {e or '(no message)'}"
        )
        return {
            "text": None,
            "latency_ms": int((time.time() - start) * 1000),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "model": local_model_name,
        }


# In-memory cache of handoff percentages (refreshed from DB periodically)
_handoff_cache: dict[str, float] = {}
_handoff_cache_ts: float = 0.0
_CACHE_TTL = 60.0  # refresh every 60s


async def _refresh_handoff_cache():
    """Load handoff percentages from DB into memory."""
    global _handoff_cache, _handoff_cache_ts
    try:
        from a1.db.engine import async_session
        from a1.db.repositories import TaskTypeReadinessRepo

        async with async_session() as session:
            repo = TaskTypeReadinessRepo(session)
            records = await repo.list_all()
            _handoff_cache = {r.task_type: r.local_handoff_pct for r in records}
            # Also cache sample counts and lifecycle state for throttling/routing
            for r in records:
                _handoff_cache[f"_count_{r.task_type}"] = r.claude_samples or 0
                _handoff_cache[f"_lifecycle_{r.task_type}"] = getattr(
                    r, "lifecycle_state", "learning"
                )
            _handoff_cache_ts = time.time()
    except Exception as e:
        log.warning(f"Failed to refresh handoff cache: {e}")


async def should_use_local(task_type: str) -> bool:
    """Decide whether to route to local model based on graduated handoff percentage.

    Uses probabilistic routing: at 30% handoff, 30% of requests go local.
    Returns True if this request should use local model.
    """
    global _handoff_cache_ts
    if time.time() - _handoff_cache_ts > _CACHE_TTL:
        await _refresh_handoff_cache()

    handoff_pct = _handoff_cache.get(task_type, 0.0)
    if handoff_pct <= 0:
        return False
    return random.random() < handoff_pct


def _transition_lifecycle(readiness, new_pct: float, improvement: float = 0.0):
    """Advance the lifecycle state machine based on current metrics.

    LEARNING → CANARY: first time handoff_pct > 0 (model has been trained and evaluated)
    CANARY → GRADUATED: handoff_pct >= 50% (sustained quality in canary)
    Any → LEARNING: regression detected (quality drops, rollback)
    """
    state = getattr(readiness, "lifecycle_state", "learning")

    if state == "learning" and new_pct > 0:
        readiness.lifecycle_state = "canary"
        readiness.canary_started_at = now_ist()
        log.info(
            f"[lifecycle] {readiness.task_type}: LEARNING → CANARY (first handoff at {new_pct:.0%})"
        )

    elif state == "canary" and new_pct >= 0.5:
        readiness.lifecycle_state = "graduated"
        readiness.graduated_at = now_ist()
        log.info(
            f"[lifecycle] {readiness.task_type}: CANARY → GRADUATED (handoff at {new_pct:.0%})"
        )

    elif state in ("canary", "graduated") and improvement < -0.1:
        # Regression: quality degraded by >10%
        readiness.lifecycle_state = "learning"
        readiness.local_handoff_pct = 0.0
        readiness.last_regression_at = now_ist()
        readiness.canary_started_at = None
        readiness.graduated_at = None
        log.warning(
            f"[lifecycle] {readiness.task_type}: {state.upper()} → LEARNING "
            f"(regression: improvement={improvement:.1%})"
        )


async def handle_dual_execution(
    request: ChatCompletionRequest,
    response: Response,
    task_type: str,
    confidence: float,
    atlas_model: str = "",
) -> ChatCompletionResponse:
    """Send request to best external provider for this atlas_model, return response,
    fire local Ollama comparison concurrently via asyncio.create_task.

    Provider selection is per-atlas-model (P1-5): atlas-plan/code → Groq,
    atlas-secure/infra → Anthropic, atlas-data/books/audit → OpenAI.
    """
    start_time = time.time()

    external_provider, provider_name, external_model = _get_external_provider(atlas_model)
    if not external_provider:
        log.warning("No external provider available, falling back to auto-routing")
        return None

    original_model = request.model
    request.model = external_model

    # Inject Atlas identity + domain suffix (claude-cli does this internally,
    # all other providers need explicit injection)
    if provider_name != "claude-cli":
        _inject_atlas_identity(request, original_model)

    # Snapshot messages for training record BEFORE provider call modifies anything
    messages_dicts = [m.model_dump(exclude_none=True) for m in request.messages]

    # P0: start local model concurrently if parallel execution is enabled.
    # User response time = external latency only (local runs in background).
    # Throttle: only run comparison on 1-in-N requests when we already
    # have enough samples (saves GPU for actual serving).
    local_task = None
    if settings.parallel_dual_execution:
        sample_count = _handoff_cache.get(f"_count_{task_type}", 0)
        # Every request if < 50 samples, 1-in-3 if 50-100,
        # 1-in-10 after 100
        if sample_count < 50:
            run_local = True
        elif sample_count < 100:
            run_local = random.random() < 0.33
        else:
            run_local = random.random() < 0.10
        if run_local:
            local_task = asyncio.create_task(_run_local_model(task_type, messages_dicts))
        else:
            log.debug(f"Skipping local comparison for {task_type} ({sample_count} samples)")

    try:
        result = await external_provider.complete(request)
    except Exception as e:
        log.error(f"External provider ({provider_name}) failed: {e}, falling back to auto-routing")
        if local_task:
            local_task.cancel()
        request.model = original_model
        return None

    latency_ms = int((time.time() - start_time) * 1000)
    external_text = result.choices[0].message.content if result.choices else ""

    result.provider = provider_name
    result.task_type = task_type
    result.routing_strategy = "distillation"

    response.headers["X-A1-Provider"] = provider_name
    response.headers["X-A1-Model"] = external_model
    response.headers["X-A1-Is-Local"] = "false"
    response.headers["X-A1-Distillation"] = "teacher"
    response.headers["X-A1-Tokens-In"] = str(result.usage.prompt_tokens)
    response.headers["X-A1-Tokens-Out"] = str(result.usage.completion_tokens)

    cost = external_provider.estimate_cost(
        result.usage.prompt_tokens, result.usage.completion_tokens, external_model
    )
    metrics.record_request(
        provider_name,
        external_model,
        task_type,
        latency_ms,
        cost,
        result.usage.prompt_tokens,
        result.usage.completion_tokens,
        is_local=False,
    )

    try:
        from a1.db.engine import async_session as _usage_session
        from a1.db.models import UsageRecord

        async with _usage_session() as session:
            async with session.begin():
                session.add(
                    UsageRecord(
                        provider=provider_name,
                        model=external_model,
                        is_local=False,
                        prompt_tokens=result.usage.prompt_tokens,
                        completion_tokens=result.usage.completion_tokens,
                        cost_usd=cost,
                        equivalent_external_cost_usd=0,
                        latency_ms=latency_ms,
                        error=False,
                        cache_hit=False,
                    )
                )
    except Exception as e:
        log.warning(f"Failed to persist usage: {e}")

    try:
        from a1.db.engine import async_session as _async_session
        from a1.db.repositories import ConversationRepo, MessageRepo, RoutingRepo

        async with _async_session() as db_session:
            async with db_session.begin():
                conv_repo = ConversationRepo(db_session)
                msg_repo = MessageRepo(db_session)
                routing_repo = RoutingRepo(db_session)
                conv = await conv_repo.create(source="distillation", user_id=None)
                seq = 0
                for m in request.messages:
                    if m.role != "system":
                        await msg_repo.add(conv.id, m.role, (m.content or "")[:500], seq)
                        seq += 1
                assistant_msg = await msg_repo.add(conv.id, "assistant", external_text[:2000], seq)
                # Capture Vertex AI grounding if present
                _grounding_meta = getattr(result, "grounding_metadata", None)
                import json as _json_mod

                await routing_repo.record(
                    message_id=assistant_msg.id,
                    provider=provider_name,
                    model=external_model,
                    strategy="distillation",
                    task_type=task_type,
                    confidence=confidence,
                    latency_ms=latency_ms,
                    prompt_tokens=result.usage.prompt_tokens,
                    completion_tokens=result.usage.completion_tokens,
                    cost_usd=cost,
                    is_local=False,
                    web_search_grounded=bool(_grounding_meta),
                    grounding_metadata=_json_mod.dumps(_grounding_meta)
                    if _grounding_meta
                    else None,  # noqa: E501
                )
    except Exception as e:
        log.warning(f"Failed to persist distillation conversation: {e}")

    # Background: compare external vs local and collect training data.
    # local_task is already running in parallel (started above); pass it through
    # so _background_local_comparison awaits it instead of re-running local model.
    asyncio.create_task(
        _background_local_comparison(
            messages_dicts=messages_dicts,
            claude_response_text=external_text,
            claude_model=external_model,
            task_type=task_type,
            claude_latency_ms=latency_ms,
            claude_prompt_tokens=result.usage.prompt_tokens,
            claude_completion_tokens=result.usage.completion_tokens,
            local_task=local_task,
        )
    )

    request.model = original_model
    return result


async def handle_dual_execution_stream(
    request: ChatCompletionRequest,
    task_type: str,
    confidence: float,
    atlas_model: str = "",
):
    """Stream response from best external provider for this atlas_model.

    Provider selection is per-atlas-model (P1-5). Fires local Ollama
    comparison concurrently. Returns async chunk iterator or None.
    """
    external_provider, provider_name, external_model = _get_external_provider(atlas_model)
    if not external_provider:
        return None

    original_model = request.model
    request.model = external_model

    if provider_name != "claude-cli":
        _inject_atlas_identity(request, original_model)

    messages_dicts = [m.model_dump(exclude_none=True) for m in request.messages]
    start_time = time.time()

    # P0: fire local model concurrently (throttled)
    local_task = None
    if settings.parallel_dual_execution:
        sample_count = _handoff_cache.get(f"_count_{task_type}", 0)
        if sample_count < 50:
            run_local = True
        elif sample_count < 100:
            run_local = random.random() < 0.33
        else:
            run_local = random.random() < 0.10
        if run_local:
            local_task = asyncio.create_task(_run_local_model(task_type, messages_dicts))

    try:
        raw_iter = external_provider.stream(request)
    except Exception as e:
        log.error(f"External provider stream ({provider_name}) failed: {e}")
        if local_task:
            local_task.cancel()
        return None

    async def _wrapped_stream():
        """Yield chunks live; fire background training task after stream ends."""
        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0
        try:
            async for chunk in raw_iter:
                if chunk.choices:
                    delta_text = chunk.choices[0].delta.content or ""
                    full_text += delta_text
                if chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                yield chunk
        finally:
            latency_ms = int((time.time() - start_time) * 1000)
            if full_text:
                asyncio.create_task(
                    _background_local_comparison(
                        messages_dicts=messages_dicts,
                        claude_response_text=full_text,
                        claude_model=external_model,
                        task_type=task_type,
                        claude_latency_ms=latency_ms,
                        claude_prompt_tokens=prompt_tokens,
                        claude_completion_tokens=completion_tokens,
                        local_task=local_task,
                    )
                )

    return _wrapped_stream()


async def _background_local_comparison(
    messages_dicts: list[dict],
    claude_response_text: str,
    claude_model: str,
    task_type: str,
    claude_latency_ms: int,
    claude_prompt_tokens: int,
    claude_completion_tokens: int,
    local_task=None,
):
    """Background task: compare external vs local response, store, maybe train.

    If local_task is provided (parallel execution mode), awaits that already-running
    coroutine instead of launching a new local model call.
    """
    try:
        local_text = None
        local_latency = 0
        local_prompt_tokens = 0
        local_completion_tokens = 0
        local_model_name = None

        if local_task is not None:
            # Parallel mode: local model was started before external call
            try:
                res = await local_task
                local_text = res.get("text")
                local_latency = res.get("latency_ms", 0)
                local_prompt_tokens = res.get("prompt_tokens", 0)
                local_completion_tokens = res.get("completion_tokens", 0)
                local_model_name = res.get("model")
            except Exception as e:
                log.warning(f"Parallel local task failed: {e}")
        else:
            # Sequential fallback: run local model now
            from a1.proxy.request_models import ChatCompletionRequest, MessageInput
            from a1.routing.strategy import select_model

            local_model_name, local_provider_name = await select_model(task_type, "best_quality")
            local_provider = provider_registry.get_provider(local_provider_name)

            if local_provider and local_provider_name == "ollama":
                local_messages = [
                    MessageInput(role=m["role"], content=m.get("content", ""))
                    for m in messages_dicts
                ]
                local_req = ChatCompletionRequest(
                    model=local_model_name,
                    messages=local_messages,
                    max_tokens=1000,
                )
                start = time.time()
                try:
                    local_result = await local_provider.complete(local_req)
                    local_latency = int((time.time() - start) * 1000)
                    local_text = (
                        local_result.choices[0].message.content if local_result.choices else ""
                    )
                    local_prompt_tokens = local_result.usage.prompt_tokens
                    local_completion_tokens = local_result.usage.completion_tokens
                except Exception as e:
                    log.warning(f"Local comparison failed for {local_model_name}: {e}")
                    local_latency = int((time.time() - start) * 1000)

        # Compute similarity
        similarity = 0.0
        if local_text and claude_response_text:
            similarity = _compute_similarity(claude_response_text, local_text)

        # Skip samples where the local student produced nothing (or zero overlap
        # with the teacher) — they pollute the training set with empty rows and
        # inflate the "samples" counter without giving the trainer anything
        # learnable. Disable via A1_DISTILLATION_SKIP_ZERO_SIMILARITY=false.
        try:
            from config.settings import settings as _s

            _skip_zero = bool(_s.distillation_skip_zero_similarity)
        except Exception:
            _skip_zero = True
        if _skip_zero and (not local_text or similarity == 0.0):
            log.info(
                f"Distillation: task={task_type} similarity={similarity:.2f} "
                f"claude={claude_latency_ms}ms local={local_latency}ms "
                "— skipped (no local output or zero similarity)"
            )
            return

        # Store dual execution record
        try:
            from a1.db.engine import async_session
            from a1.db.repositories import DualExecutionRepo, TaskTypeReadinessRepo

            async with async_session() as session:
                async with session.begin():
                    repo = DualExecutionRepo(session)
                    await repo.create(
                        task_type=task_type,
                        request_messages=messages_dicts,
                        claude_model=claude_model,
                        claude_response=claude_response_text,
                        claude_latency_ms=claude_latency_ms,
                        claude_prompt_tokens=claude_prompt_tokens,
                        claude_completion_tokens=claude_completion_tokens,
                        local_model=local_model_name if local_text else None,
                        local_response=local_text,
                        local_latency_ms=local_latency if local_text else None,
                        local_prompt_tokens=local_prompt_tokens if local_text else None,
                        local_completion_tokens=local_completion_tokens if local_text else None,
                        similarity_score=similarity if local_text else None,
                        quality_score=similarity
                        if local_text
                        else None,  # derived from Jaccard (fix 2.7)
                    )

                    # Increment sample count
                    readiness_repo = TaskTypeReadinessRepo(session)
                    count = await readiness_repo.increment_sample_count(task_type)

                    log.info(
                        f"Distillation: task={task_type} similarity={similarity:.2f} "
                        f"claude={claude_latency_ms}ms local={local_latency}ms "
                        f"samples={count}"
                    )

                    # Check if training should be triggered
                    await _check_and_trigger_training(session, task_type, count)

        except Exception as e:
            log.error(f"Failed to store dual execution record: {e}")

    except Exception as e:
        log.error(f"Background local comparison error: {e}")


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "it",
        "in",
        "on",
        "at",
        "to",
        "of",
        "and",
        "or",
        "for",
        "with",
        "this",
        "that",
        "was",
        "be",
        "are",
        "i",
        "you",
        "we",
        "he",
        "she",
        "they",
        "as",
        "by",
        "from",
        "but",
        "not",
        "so",
        "if",
        "do",
        "did",
        "has",
        "have",
        "had",
        "can",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "its",
        "their",
        "our",
        "your",
        "his",
        "her",
        "my",
        "what",
        "how",
        "when",
        "where",
        "which",
        "who",
        "all",
        "more",
        "also",
        "just",
        "than",
        "then",
        "there",
        "these",
        "those",
        "been",
        "into",
        "out",
        "up",
        "about",
    }
)


def _lcs_length(words_a: list[str], words_b: list[str]) -> int:
    """Compute Longest Common Subsequence length between two word lists."""
    m, n = len(words_a), len(words_b)
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if words_a[i - 1] == words_b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(curr[j - 1], prev[j])
        prev = curr
    return prev[n]


def _rouge_l(words_a: list[str], words_b: list[str]) -> float:
    """ROUGE-L F1 score using LCS at word level."""
    if not words_a or not words_b:
        return 0.0
    lcs = _lcs_length(words_a, words_b)
    precision = lcs / len(words_b)
    recall = lcs / len(words_a)
    denom = precision + recall
    return (2 * precision * recall / denom) if denom > 0 else 0.0


def _compute_similarity(text_a: str, text_b: str) -> float:
    """Compute semantic similarity between two texts.

    Combines stopword-filtered Jaccard (bag-of-words overlap) with ROUGE-L
    (order-aware LCS recall). More accurate than plain Jaccard — less inflated
    by stopwords, captures phrasing order for distillation quality scoring.
    """
    if not text_a or not text_b:
        return 0.0

    # Stopword-filtered word lists (cap at 500 words each for LCS performance)
    raw_a = text_a.lower().split()[:500]
    raw_b = text_b.lower().split()[:500]
    filtered_a = [w for w in raw_a if w not in _STOPWORDS]
    filtered_b = [w for w in raw_b if w not in _STOPWORDS]

    # Filtered Jaccard
    set_a = set(filtered_a)
    set_b = set(filtered_b)
    if set_a and set_b:
        inter = set_a & set_b
        union = set_a | set_b
        jaccard = len(inter) / len(union)
    else:
        jaccard = 0.0

    # ROUGE-L on filtered word lists
    rouge = _rouge_l(filtered_a, filtered_b)

    # Length ratio (penalize very different response lengths)
    len_ratio = min(len(text_a), len(text_b)) / max(len(text_a), len(text_b))

    return round(0.4 * jaccard + 0.4 * rouge + 0.2 * len_ratio, 3)


async def _check_and_trigger_training(session: AsyncSession, task_type: str, sample_count: int):
    """Check if enough distillation samples exist and trigger training."""
    if sample_count < settings.distillation_min_samples:
        return

    # Check if training is already running
    from a1.db.repositories import TrainingRepo

    repo = TrainingRepo(session)
    runs = await repo.list_runs(limit=5)
    for run in runs:
        if (
            run.status in ("pending", "running")
            and run.config
            and run.config.get("task_type") == task_type
        ):
            return  # Already training for this task type

    # Check if we've trained recently (within 1 hour)
    from a1.db.repositories import TaskTypeReadinessRepo

    readiness_repo = TaskTypeReadinessRepo(session)
    readiness = await readiness_repo.get_or_create(task_type)
    if readiness.last_evaluated_at:
        from datetime import timedelta

        if now_ist() - readiness.last_evaluated_at < timedelta(hours=1):
            return  # Trained recently

    # Trigger training!
    log.info(f"Auto-triggering training for task_type={task_type} ({sample_count} samples)")

    # Mark the records being included in this training batch (fix 2.7)
    from a1.db.repositories import DualExecutionRepo

    dual_repo = DualExecutionRepo(session)
    # Quality gate: only use samples meeting the distillation quality threshold
    training_records = await dual_repo.get_unused_for_training(
        task_type,
        min_quality=settings.distillation_quality_threshold,
        limit=sample_count,
    )
    for rec in training_records:
        rec.used_for_training = True
    log.info(
        f"Marked {len(training_records)} records as used_for_training for {task_type}"
        f" (quality_gate >= {settings.distillation_quality_threshold})"
    )

    config = {
        "base_model": settings.training_base_model,
        "lora_rank": settings.training_lora_rank,
        "epochs": 3,
        "task_type": task_type,
        "distillation": True,
    }
    run = await repo.create_run(
        base_model=config["base_model"],
        dataset_size=sample_count,
        config=config,
    )
    readiness.last_evaluated_at = now_ist()
    readiness.last_training_run_id = str(run.id)

    log.info(f"Training run created: {run.id} for {task_type}")
    from a1.dependencies import get_arq_pool

    arq_pool = await get_arq_pool()
    await arq_pool.enqueue_job("run_training_pipeline", str(run.id))
    log.info(f"Training job dispatched to ARQ: run_id={run.id}")


async def update_handoff_after_training(task_type: str, eval_results: dict):
    """After a training run completes, update the handoff percentage.

    If Argilla handoff gate is enabled, the increment is not applied immediately.
    Instead, the current sample batch is pushed to Argilla for human annotation,
    the task_type_readiness row is marked as `pending_argilla_review`, and the
    handoff % is left unchanged until `check_and_apply_argilla_approvals` confirms
    that >= argilla_approval_threshold fraction of annotated records are rated ≥4/5.
    """
    try:
        from a1.db.engine import async_session
        from a1.db.repositories import TaskTypeReadinessRepo

        improved = eval_results.get("improved", False)
        improvement = eval_results.get("improvement", 0.0)

        async with async_session() as session:
            async with session.begin():
                repo = TaskTypeReadinessRepo(session)
                readiness = await repo.get_or_create(task_type)

                if improved and improvement > 0.02:  # >2% improvement
                    # --- Argilla quality gate ---
                    if settings.argilla_handoff_gate_enabled and settings.argilla_api_url:
                        # Don't increment yet — queue for human review
                        from a1.feedback.argilla_sync import push_handoff_batch_for_review

                        batch_id = await push_handoff_batch_for_review(session, task_type)
                        readiness.argilla_review_status = "pending_argilla_review"
                        readiness.argilla_batch_id = batch_id
                        readiness.local_avg_quality = eval_results.get("avg_finetuned_loss", 0.0)
                        log.info(
                            f"Handoff for {task_type} pending Argilla review: batch_id={batch_id} "
                            f"(improvement: {improvement:.1%})"
                        )
                        # Store pending increment magnitude so we can apply it after approval
                        # We encode it in readiness but don't touch local_handoff_pct
                        _handoff_cache[task_type] = readiness.local_handoff_pct  # unchanged
                        return

                    # --- Eval-set quality gate (Phase 3.1) ---
                    # Block the handoff increase if the new local model fails an
                    # eval-set replay, even when training loss "improved".
                    if settings.distillation_eval_gate_enabled:
                        from a1.eval.gate import run_eval_gate

                        local_model = (
                            getattr(readiness, "local_model", None)
                            or eval_results.get("ollama_model")
                            or eval_results.get("local_model")
                        )
                        if local_model:
                            gate = await run_eval_gate(
                                task_type,
                                local_model,
                                settings.distillation_eval_gate_min_score,
                            )
                            if not gate["passed"]:
                                readiness.argilla_review_status = "eval_gate_failed"
                                log.warning(
                                    f"Handoff for {task_type} BLOCKED by eval gate: "
                                    f"avg_judge={gate['avg_judge']} "
                                    f"avg_heuristic={gate['avg_heuristic']} "
                                    f"min={settings.distillation_eval_gate_min_score} "
                                    f"dataset={gate['dataset']}"
                                )
                                return

                    # Gate disabled (or passed) — apply increment directly
                    cap = (
                        getattr(readiness, "max_local_pct", None)
                        or settings.distillation_max_handoff_pct
                    )
                    old_pct = readiness.local_handoff_pct
                    new_pct = min(
                        old_pct + settings.distillation_handoff_increment,
                        cap,
                    )
                    readiness.local_handoff_pct = new_pct
                    readiness.local_avg_quality = eval_results.get("avg_finetuned_loss", 0.0)

                    # P3: Lifecycle state transitions
                    _transition_lifecycle(readiness, new_pct, improvement)

                    log.info(
                        f"Handoff increased for {task_type}: "
                        f"{old_pct:.0%} → {new_pct:.0%} "
                        f"(improvement: {improvement:.1%}, lifecycle: {readiness.lifecycle_state})"
                    )
                else:
                    log.info(
                        f"No handoff change for {task_type} "
                        f"(improved={improved}, improvement={improvement:.1%})"
                    )

                # Refresh in-memory cache
                _handoff_cache[task_type] = readiness.local_handoff_pct

    except Exception as e:
        log.error(f"Failed to update handoff: {e}")


async def check_and_apply_argilla_approvals():
    """Scan all task_type_readiness rows in `pending_argilla_review` state and apply
    handoff increments for batches that have reached the annotation approval threshold.

    Intended to be called periodically (e.g. from an ARQ scheduled job or admin endpoint).
    """
    try:
        from a1.db.engine import async_session
        from a1.db.repositories import TaskTypeReadinessRepo
        from a1.feedback.argilla_sync import check_handoff_batch_approval

        async with async_session() as session:
            async with session.begin():
                repo = TaskTypeReadinessRepo(session)
                all_readiness = await repo.list_all()

                for readiness in all_readiness:
                    if readiness.argilla_review_status != "pending_argilla_review":
                        continue
                    if not readiness.argilla_batch_id:
                        continue

                    result = await check_handoff_batch_approval(readiness.argilla_batch_id)

                    if result["pending"]:
                        log.info(
                            f"Argilla review pending for {readiness.task_type}: "
                            f"{result['annotated_count']}/{result['total_records']}"
                            " annotated so far"
                        )
                        continue

                    if result["approved"]:
                        cap = (
                            getattr(readiness, "max_local_pct", None)
                            or settings.distillation_max_handoff_pct
                        )
                        old_pct = readiness.local_handoff_pct
                        new_pct = min(old_pct + settings.distillation_handoff_increment, cap)
                        readiness.local_handoff_pct = new_pct
                        readiness.argilla_review_status = "approved"
                        _handoff_cache[readiness.task_type] = new_pct
                        log.info(
                            f"Argilla approved handoff increment for {readiness.task_type}: "
                            f"{old_pct:.0%} → {new_pct:.0%} "
                            f"({result['positive_pct']:.0%} positive, "
                            f"{result['annotated_count']} annotations)"
                        )
                    else:
                        readiness.argilla_review_status = "rejected"
                        log.warning(
                            f"Argilla rejected handoff increment for {readiness.task_type}: "
                            f"{result['positive_pct']:.0%} positive "
                            f"(threshold {settings.argilla_approval_threshold:.0%}), "
                            f"{result['annotated_count']} annotations"
                        )

    except Exception as e:
        log.error(f"Failed during Argilla approval check: {e}")
