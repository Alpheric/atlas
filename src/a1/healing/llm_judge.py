"""Layer 1b — LLM-as-judge quality scorer (Phase 2.4).

A richer quality signal than the heuristic scorer. Asks a cheap/fast model to
rate a response 0–100 for the given task type and returns a 0–1 score. Stored as
a QualitySignal(signal_type="auto_eval", evaluator="llm_judge_v1") so it lives
alongside the heuristic ("heuristic_v1") and can be compared/averaged.

Gated by settings.quality_llm_judge_enabled and sampled by
quality_llm_judge_sample_rate. Runs fire-and-forget off the request path, so it
never adds latency to the user's response. All errors are swallowed.

The judge prompt is loaded via the prompt registry (name "llm_judge"), so it can
be A/B-tested without a redeploy; falls back to the default below.
"""

from __future__ import annotations

import json
import random
import re

from a1.common.logging import get_logger
from config.settings import settings

log = get_logger("healing.llm_judge")

_JUDGE_PROMPT = """\
You are a strict quality evaluator. Rate the ASSISTANT RESPONSE below for a \
"{task_type}" task on a 0–100 scale, where:
- 0–40: incorrect, refusing, truncated, or off-task
- 41–70: usable but incomplete, generic, or poorly formatted
- 71–100: accurate, complete, well-structured, directly useful

Consider correctness, completeness, structure, and whether it fits the task type \
(e.g. code tasks should include working code blocks).

Respond with ONLY a compact JSON object, no prose:
{{"score": <int 0-100>, "reason": "<one short sentence>"}}

=== ASSISTANT RESPONSE ===
{response_text}
"""

_SCORE_RE = re.compile(r'"score"\s*:\s*(\d{1,3})')


async def judge_response(
    response_text: str,
    task_type: str,
    provider,
    model: str,
) -> tuple[float, str] | None:
    """Return (score_0_1, reason) from the judge model, or None on failure."""
    from a1.common.prompt_registry import get_prompt
    from a1.proxy.request_models import ChatCompletionRequest

    if not response_text or not response_text.strip():
        return None

    template = await get_prompt("llm_judge", default=_JUDGE_PROMPT)
    try:
        prompt = template.format(task_type=task_type, response_text=response_text[:4000])
    except (KeyError, IndexError):
        prompt = _JUDGE_PROMPT.format(task_type=task_type, response_text=response_text[:4000])

    try:
        req = ChatCompletionRequest(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.0,
        )
        resp = await provider.complete(req)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
    except Exception as e:
        log.debug(f"[llm-judge] inference failed: {e}")
        return None

    # Parse JSON; fall back to a regex over the score field.
    score_int = None
    reason = ""
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            score_int = int(obj.get("score"))
            reason = str(obj.get("reason", ""))[:200]
    except Exception:
        pass
    if score_int is None:
        m = _SCORE_RE.search(text)
        if m:
            score_int = int(m.group(1))
    if score_int is None:
        log.debug(f"[llm-judge] could not parse score from: {text[:80]!r}")
        return None

    score = max(0.0, min(score_int, 100)) / 100.0
    return round(score, 3), reason


async def judge_and_store(
    response_text: str,
    task_type: str,
    message_id: str,
) -> float | None:
    """Run the judge (if enabled + sampled) and persist a QualitySignal.

    Fire-and-forget friendly. Resolves a cheap judge provider internally.
    Returns the 0–1 score, or None if disabled/sampled-out/failed.
    """
    if not settings.quality_llm_judge_enabled:
        return None
    if random.random() > settings.quality_llm_judge_sample_rate:
        return None

    try:
        from a1.providers.registry import provider_registry

        provider = provider_registry.get_provider("claude-cli")
        if not provider or not provider_registry.is_healthy("claude-cli"):
            return None

        result = await judge_response(
            response_text, task_type, provider, settings.quality_llm_judge_model
        )
        if result is None:
            return None
        score, reason = result

        import uuid as _uuid

        from a1.db.engine import async_session
        from a1.db.models import QualitySignal

        async with async_session() as db:
            async with db.begin():
                db.add(
                    QualitySignal(
                        message_id=_uuid.UUID(message_id)
                        if isinstance(message_id, str)
                        else message_id,
                        signal_type="auto_eval",
                        value=score,
                        evaluator="llm_judge_v1",
                    )
                )
        log.debug(f"[llm-judge] score={score:.3f} task={task_type} reason={reason!r}")
        return score
    except Exception as e:
        log.debug(f"[llm-judge] judge_and_store skipped: {e}")
        return None
