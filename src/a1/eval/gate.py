"""Distillation quality-regression gate (Phase 3.1).

Before a fine-tuned local model is allowed to take more traffic (handoff %
increase / lifecycle graduation), replay an eval dataset through it and require
the average LLM-judge score to clear a threshold. This blocks a regressed
adapter from graduating even if its training loss "improved".

Reuses the Phase 2.3 eval items + Phase 2.4 LLM judge. If no eval dataset exists
for the task type, the gate is skipped (returns passed=True) so training is
never blocked purely on missing eval data — that's logged so it's visible.
"""

from __future__ import annotations

from sqlalchemy import select

from a1.common.logging import get_logger

log = get_logger("eval.gate")


async def run_eval_gate(task_type: str, model: str, min_score: float) -> dict:
    """Replay the task's eval dataset through `model`; gate on avg judge score.

    Returns dict: {passed, reason, avg_judge, avg_heuristic, item_count, dataset}.
    """
    from a1.db.engine import async_session
    from a1.db.models import EvalDataset, EvalItem
    from a1.healing.llm_judge import judge_response
    from a1.healing.quality_scorer import score_response
    from a1.providers.registry import provider_registry
    from a1.proxy.request_models import ChatCompletionRequest
    from config.settings import settings

    # Resolve the dataset: explicit name, else first dataset whose task_type matches.
    async with async_session() as db:
        ds = None
        if settings.distillation_eval_gate_dataset:
            ds = (
                await db.execute(
                    select(EvalDataset).where(
                        EvalDataset.name == settings.distillation_eval_gate_dataset
                    )
                )
            ).scalar_one_or_none()
        if ds is None:
            ds = (
                await db.execute(
                    select(EvalDataset).where(EvalDataset.task_type == task_type)
                )
            ).scalars().first()

        if ds is None:
            log.info(f"[eval-gate] no eval dataset for task '{task_type}' — skipping gate (pass)")
            return {
                "passed": True,
                "reason": "no_eval_dataset",
                "avg_judge": None,
                "avg_heuristic": None,
                "item_count": 0,
                "dataset": None,
            }

        items = (
            await db.execute(select(EvalItem).where(EvalItem.dataset_id == ds.id))
        ).scalars().all()
        item_data = [(it.input_messages, it.task_type or task_type) for it in items]
        ds_name = ds.name

    if not item_data:
        log.info(f"[eval-gate] dataset '{ds_name}' empty — skipping gate (pass)")
        return {
            "passed": True,
            "reason": "empty_dataset",
            "avg_judge": None,
            "avg_heuristic": None,
            "item_count": 0,
            "dataset": ds_name,
        }

    provider = provider_registry.get_provider_for_model(model)
    if provider is None or not hasattr(provider, "complete"):
        log.warning(f"[eval-gate] no provider for model '{model}' — failing gate")
        return {
            "passed": False,
            "reason": f"no_provider_for_{model}",
            "avg_judge": None,
            "avg_heuristic": None,
            "item_count": 0,
            "dataset": ds_name,
        }

    judge_provider = provider_registry.get_provider("claude-cli")
    judge_ok = bool(judge_provider and provider_registry.is_healthy("claude-cli"))

    h_scores: list[float] = []
    j_scores: list[float] = []
    for messages, t_type in item_data:
        try:
            req = ChatCompletionRequest(
                model=model, messages=messages, max_tokens=1024, temperature=0.0
            )
            resp = await provider.complete(req)
            text = (resp.choices[0].message.content or "") if resp.choices else ""
        except Exception as e:
            log.debug(f"[eval-gate] item failed: {e}")
            continue
        h_scores.append(score_response(text, t_type))
        if judge_ok:
            try:
                jr = await judge_response(
                    text, t_type, judge_provider, settings.quality_llm_judge_model
                )
                if jr:
                    j_scores.append(jr[0])
            except Exception:
                pass

    avg_h = round(sum(h_scores) / len(h_scores), 4) if h_scores else None
    avg_j = round(sum(j_scores) / len(j_scores), 4) if j_scores else None

    # Gate on judge score when available, else fall back to heuristic.
    gate_score = avg_j if avg_j is not None else avg_h
    passed = gate_score is not None and gate_score >= min_score

    log.info(
        f"[eval-gate] task={task_type} model={model} dataset={ds_name} "
        f"avg_judge={avg_j} avg_heuristic={avg_h} min={min_score} → "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return {
        "passed": passed,
        "reason": "gate_score_below_min" if not passed else "ok",
        "avg_judge": avg_j,
        "avg_heuristic": avg_h,
        "item_count": len(h_scores),
        "dataset": ds_name,
    }
