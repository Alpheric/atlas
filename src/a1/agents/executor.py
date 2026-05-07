"""Agent Executor — runs an agent for a single task turn.

Merges the agent's system prompt + tools into a ChatCompletionRequest,
routes through the CorePipeline (atlas_router), records an AgentExecution
audit row, and returns the assistant text.
"""

import asyncio
import time

from a1.agents.registry import AgentDefinition
from a1.common.logging import get_logger
from a1.proxy.request_models import ChatCompletionRequest, MessageInput

log = get_logger("agents.executor")


async def run_agent(
    agent: AgentDefinition,
    task: str,
    extra_messages: list[dict] | None = None,
    max_tokens: int = 2000,
    stream: bool = False,
) -> str | None:
    """Execute an agent for a single task.

    Builds a ChatCompletionRequest with the agent's persona injected as the
    system prompt, then routes through the Atlas distillation pipeline.

    Resilience:
    - Hard timeout of settings.agent_builder_timeout_s (default 55s) prevents
      slow claude-cli calls from causing Cloudflare 520 errors.
    - If settings.agent_builder_openai_fallback is True and OpenAI is healthy,
      a failed/timed-out primary execution is retried on gpt-4o automatically.

    Records the execution in agent_executions for audit.
    Returns assistant text, or None on failure.
    """
    start = time.time()
    result_text = None
    error_text = None

    from fastapi.responses import Response

    from a1.routing.classifier import classify_task
    from a1.training.auto_trainer import handle_dual_execution
    from config.settings import settings

    # Build messages once — reused for both primary and fallback attempts
    system_parts = []
    if agent.system_prompt:
        system_parts.append(agent.system_prompt)
    if agent.tools:
        tool_lines = "\n".join(f"- {t}" for t in agent.tools)
        system_parts.append(f"\nAvailable tools:\n{tool_lines}")

    messages: list[MessageInput] = []
    if system_parts:
        messages.append(MessageInput(role="system", content="\n\n".join(system_parts)))

    for m in extra_messages or []:
        messages.append(MessageInput(role=m.get("role", "user"), content=m.get("content", "")))

    messages.append(MessageInput(role="user", content=task))

    req = ChatCompletionRequest(
        model=agent.atlas_model,
        messages=messages,
        max_tokens=max_tokens,
    )

    response_obj = Response()
    task_type, _ = classify_task(req)
    timeout = settings.agent_builder_timeout_s

    # ── Primary execution (distillation pipeline) ─────────────────────────
    try:
        result = await asyncio.wait_for(
            handle_dual_execution(req, response_obj, task_type, 0.9, atlas_model=agent.atlas_model),
            timeout=float(timeout),
        )
        if result and result.choices:
            result_text = result.choices[0].message.content

    except asyncio.TimeoutError:
        log.warning(
            f"[agent:{agent.name}] Primary execution timed out after {timeout}s"
            + (" — attempting OpenAI fallback" if settings.agent_builder_openai_fallback else "")
        )
        error_text = f"Primary provider timed out after {timeout}s"

    except Exception as e:
        log.error(
            f"[agent:{agent.name}] Primary execution error: {e}"
            + (" — attempting OpenAI fallback" if settings.agent_builder_openai_fallback else "")
        )
        error_text = str(e)

    # ── OpenAI fallback (opt-in) ──────────────────────────────────────────
    if result_text is None and settings.agent_builder_openai_fallback:
        result_text = await _openai_fallback(agent, messages, max_tokens, task_type)
        if result_text:
            error_text = None  # clear error — fallback succeeded
            log.info(f"[agent:{agent.name}] OpenAI fallback succeeded (task_type={task_type})")

    latency_ms = int((time.time() - start) * 1000)

    # Fire-and-forget audit record
    asyncio.create_task(
        _record_execution(
            agent_id=agent.id,
            task=task,
            result=result_text,
            latency_ms=latency_ms,
            error=error_text,
        )
    )

    return result_text


async def _openai_fallback(
    agent: "AgentDefinition",
    messages: list[MessageInput],
    max_tokens: int,
    task_type: str,
) -> str | None:
    """Attempt to run the agent task via OpenAI gpt-4o.

    Used when the primary provider (claude-cli / Alpheric distillation) times out
    or errors.  Only called when settings.agent_builder_openai_fallback is True.
    Returns assistant text or None if OpenAI is unavailable or also errors.
    """
    from a1.providers.registry import provider_registry

    openai_provider = provider_registry.get_provider("openai")
    if not openai_provider or not provider_registry.is_healthy("openai"):
        log.warning(f"[agent:{agent.name}] OpenAI fallback requested but provider not healthy")
        return None

    try:
        from a1.proxy.request_models import ChatCompletionRequest

        # gpt-4o is the best quality/cost tradeoff for agent tasks; o3-mini for reasoning
        fallback_model = "o3-mini" if task_type in ("reasoning", "math") else "gpt-4o"

        fb_req = ChatCompletionRequest(
            model=fallback_model,
            messages=messages,
            max_tokens=max_tokens,
        )
        resp = await asyncio.wait_for(openai_provider.complete(fb_req), timeout=45.0)
        if resp.choices:
            return resp.choices[0].message.content
    except Exception as e:
        log.error(f"[agent:{agent.name}] OpenAI fallback also failed: {e}")

    return None


async def run_agent_by_id(
    agent_id: str,
    task: str,
    extra_messages: list[dict] | None = None,
    max_tokens: int = 2000,
) -> str | None:
    """Convenience wrapper — looks up agent by ID then runs it."""
    from a1.agents.registry import agent_registry

    agent = agent_registry.get_by_id(agent_id)
    if not agent:
        log.warning(f"[executor] Agent {agent_id} not found in registry")
        return None
    if agent.status != "active":
        log.warning(f"[executor] Agent {agent_id} is {agent.status}, skipping")
        return None
    return await run_agent(agent, task, extra_messages=extra_messages, max_tokens=max_tokens)


async def _record_execution(
    agent_id: str,
    task: str,
    result: str | None,
    latency_ms: int,
    error: str | None,
):
    """Persist AgentExecution audit row (runs as background task)."""
    try:
        import uuid as _uuid

        from a1.db.engine import async_session
        from a1.db.models import AgentExecution

        async with async_session() as session:
            async with session.begin():
                row = AgentExecution(
                    id=_uuid.uuid4(),
                    agent_id=_uuid.UUID(agent_id),
                    task=task[:4096],
                    result=result[:8192] if result else None,
                    latency_ms=latency_ms,
                    error=error,
                )
                session.add(row)
    except Exception as e:
        log.debug(f"Failed to record agent execution: {e}")
