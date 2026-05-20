"""Eval experiment runner (Phase 2.3).

Runs every item in an eval dataset through a target model, scores each response
with the heuristic scorer and (optionally) the LLM judge, and writes aggregate
results back to the EvalRun row. Designed to run as a fire-and-forget background
task so the trigger endpoint returns immediately.

Also provides promote_from_distillation(): curate high-quality
DualExecutionRecord rows into a versioned eval dataset.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select

from a1.common.logging import get_logger
from a1.common.tz import now_ist

log = get_logger("eval.runner")


async def run_eval(run_id: str) -> None:
    """Execute an EvalRun: stream each item through the model, score, aggregate."""
    from a1.db.engine import async_session
    from a1.db.models import EvalItem, EvalRun
    from a1.healing.llm_judge import judge_response
    from a1.healing.quality_scorer import score_response
    from a1.providers.registry import provider_registry
    from a1.proxy.request_models import ChatCompletionRequest
    from config.settings import settings

    run_uuid = uuid.UUID(run_id) if isinstance(run_id, str) else run_id

    # Load the run + its dataset items
    async with async_session() as db:
        run = (
            await db.execute(select(EvalRun).where(EvalRun.id == run_uuid))
        ).scalar_one_or_none()
        if not run:
            log.warning(f"[eval] run {run_id} not found")
            return
        model = run.model
        dataset_id = run.dataset_id
        items = (
            await db.execute(select(EvalItem).where(EvalItem.dataset_id == dataset_id))
        ).scalars().all()
        # snapshot item data so we don't hold the session across inference
        item_data = [
            (str(it.id), it.input_messages, it.task_type or "general") for it in items
        ]

    # Mark running
    async with async_session() as db:
        async with db.begin():
            r = (await db.execute(select(EvalRun).where(EvalRun.id == run_uuid))).scalar_one()
            r.status = "running"
            r.item_count = len(item_data)

    provider = provider_registry.get_provider_for_model(model)
    if provider is None or not hasattr(provider, "complete"):
        async with async_session() as db:
            async with db.begin():
                r = (await db.execute(select(EvalRun).where(EvalRun.id == run_uuid))).scalar_one()
                r.status = "failed"
                r.error = f"No chat provider serves model '{model}'"
                r.completed_at = now_ist()
        return

    judge_provider = provider_registry.get_provider("claude-cli")
    judge_ok = bool(judge_provider and provider_registry.is_healthy("claude-cli"))

    results: list[dict] = []
    h_scores: list[float] = []
    j_scores: list[float] = []
    latencies: list[float] = []

    for item_id, messages, task_type in item_data:
        try:
            req = ChatCompletionRequest(
                model=model, messages=messages, max_tokens=1024, temperature=0.0
            )
            t0 = time.time()
            resp = await provider.complete(req)
            latency = (time.time() - t0) * 1000
            text = (resp.choices[0].message.content or "") if resp.choices else ""
        except Exception as e:
            results.append({"item_id": item_id, "error": str(e)[:200]})
            continue

        h = score_response(text, task_type)
        h_scores.append(h)
        latencies.append(latency)

        j = None
        if judge_ok:
            try:
                jr = await judge_response(
                    text, task_type, judge_provider, settings.quality_llm_judge_model
                )
                if jr:
                    j = jr[0]
                    j_scores.append(j)
            except Exception:
                pass

        results.append(
            {
                "item_id": item_id,
                "task_type": task_type,
                "heuristic": h,
                "judge": j,
                "latency_ms": round(latency, 1),
                "response_preview": text[:200],
            }
        )

    avg_h = round(sum(h_scores) / len(h_scores), 4) if h_scores else None
    avg_j = round(sum(j_scores) / len(j_scores), 4) if j_scores else None
    avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else None

    async with async_session() as db:
        async with db.begin():
            r = (await db.execute(select(EvalRun).where(EvalRun.id == run_uuid))).scalar_one()
            r.status = "completed"
            r.avg_heuristic = avg_h
            r.avg_judge = avg_j
            r.avg_latency_ms = avg_lat
            r.results = {"items": results}
            r.completed_at = now_ist()
    log.info(
        f"[eval] run {run_id} done: model={model} items={len(item_data)} "
        f"avg_heuristic={avg_h} avg_judge={avg_j}"
    )


async def promote_from_distillation(
    dataset_name: str,
    task_type: str | None = None,
    min_quality: float = 0.7,
    limit: int = 100,
    description: str | None = None,
) -> dict:
    """Create (or append to) an eval dataset from high-quality dual-execution
    records. Uses the teacher (Claude) response as the reference output."""
    from a1.db.engine import async_session
    from a1.db.models import DualExecutionRecord, EvalDataset, EvalItem

    async with async_session() as db:
        async with db.begin():
            ds = (
                await db.execute(
                    select(EvalDataset).where(EvalDataset.name == dataset_name)
                )
            ).scalar_one_or_none()
            if ds is None:
                ds = EvalDataset(
                    id=uuid.uuid4(),
                    name=dataset_name,
                    description=description or "Promoted from distillation records",
                    task_type=task_type,
                )
                db.add(ds)
                await db.flush()
            dataset_id = ds.id

            q = select(DualExecutionRecord).where(
                DualExecutionRecord.quality_score >= min_quality
            )
            if task_type:
                q = q.where(DualExecutionRecord.task_type == task_type)
            q = q.order_by(DualExecutionRecord.quality_score.desc()).limit(limit)
            records = (await db.execute(q)).scalars().all()

            added = 0
            for rec in records:
                db.add(
                    EvalItem(
                        id=uuid.uuid4(),
                        dataset_id=dataset_id,
                        input_messages=rec.request_messages,
                        reference_output=rec.claude_response,
                        task_type=rec.task_type,
                        source="distillation",
                    )
                )
                added += 1

    log.info(f"[eval] promoted {added} items into dataset '{dataset_name}'")
    return {"dataset": dataset_name, "items_added": added}
