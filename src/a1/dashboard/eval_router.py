"""Eval datasets + experiment runs API (Phase 2.3).

Manage eval datasets, promote distillation records into them, trigger eval runs
(a dataset replayed through a model, scored by heuristic + LLM judge), and read
results.
"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.logging import get_logger
from a1.db.models import EvalDataset, EvalItem, EvalRun
from a1.dependencies import get_db

log = get_logger("dashboard.eval")
router = APIRouter(prefix="/eval", tags=["eval"])


class DatasetCreate(BaseModel):
    name: str
    description: str | None = None
    task_type: str | None = None


class ItemCreate(BaseModel):
    input_messages: list[dict]
    reference_output: str | None = None
    task_type: str | None = None


class PromoteRequest(BaseModel):
    dataset_name: str
    task_type: str | None = None
    min_quality: float = 0.7
    limit: int = 100
    description: str | None = None


class RunCreate(BaseModel):
    dataset_id: str
    model: str


# ── Datasets ────────────────────────────────────────────────────────────────


@router.get("/datasets")
async def list_datasets(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(EvalDataset).order_by(EvalDataset.created_at.desc()))
    ).scalars().all()
    out = []
    for ds in rows:
        n = (
            await db.execute(
                select(func.count()).select_from(EvalItem).where(EvalItem.dataset_id == ds.id)
            )
        ).scalar()
        out.append(
            {
                "id": str(ds.id),
                "name": ds.name,
                "description": ds.description,
                "task_type": ds.task_type,
                "item_count": int(n or 0),
                "created_at": ds.created_at.isoformat() if ds.created_at else None,
            }
        )
    return {"data": out, "total": len(out)}


@router.post("/datasets")
async def create_dataset(body: DatasetCreate, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(select(EvalDataset).where(EvalDataset.name == body.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Dataset '{body.name}' already exists")
    ds = EvalDataset(
        id=uuid.uuid4(), name=body.name, description=body.description, task_type=body.task_type
    )
    db.add(ds)
    await db.commit()
    return {"id": str(ds.id), "name": ds.name}


@router.post("/datasets/{dataset_id}/items")
async def add_item(dataset_id: str, body: ItemCreate, db: AsyncSession = Depends(get_db)):
    ds_uuid = uuid.UUID(dataset_id)
    ds = (
        await db.execute(select(EvalDataset).where(EvalDataset.id == ds_uuid))
    ).scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    item = EvalItem(
        id=uuid.uuid4(),
        dataset_id=ds_uuid,
        input_messages=body.input_messages,
        reference_output=body.reference_output,
        task_type=body.task_type or ds.task_type,
        source="manual",
    )
    db.add(item)
    await db.commit()
    return {"id": str(item.id), "dataset_id": dataset_id}


@router.post("/datasets/promote-from-distillation")
async def promote(body: PromoteRequest):
    """Curate high-quality dual-execution records into an eval dataset."""
    from a1.eval.runner import promote_from_distillation

    return await promote_from_distillation(
        dataset_name=body.dataset_name,
        task_type=body.task_type,
        min_quality=body.min_quality,
        limit=body.limit,
        description=body.description,
    )


# ── Runs ────────────────────────────────────────────────────────────────────


@router.post("/runs")
async def create_run(body: RunCreate, db: AsyncSession = Depends(get_db)):
    """Trigger an eval run (background). Returns the run id immediately."""
    ds_uuid = uuid.UUID(body.dataset_id)
    ds = (
        await db.execute(select(EvalDataset).where(EvalDataset.id == ds_uuid))
    ).scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Dataset not found")

    run = EvalRun(id=uuid.uuid4(), dataset_id=ds_uuid, model=body.model, status="pending")
    db.add(run)
    await db.commit()

    from a1.eval.runner import run_eval

    asyncio.create_task(run_eval(str(run.id)))
    return {"run_id": str(run.id), "status": "pending", "model": body.model}


@router.get("/runs")
async def list_runs(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(100))
    ).scalars().all()
    return {
        "data": [
            {
                "id": str(r.id),
                "dataset_id": str(r.dataset_id),
                "model": r.model,
                "status": r.status,
                "item_count": r.item_count,
                "avg_heuristic": r.avg_heuristic,
                "avg_judge": r.avg_judge,
                "avg_latency_ms": r.avg_latency_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (
        await db.execute(select(EvalRun).where(EvalRun.id == uuid.UUID(run_id)))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Run not found")
    return {
        "id": str(r.id),
        "dataset_id": str(r.dataset_id),
        "model": r.model,
        "status": r.status,
        "item_count": r.item_count,
        "avg_heuristic": r.avg_heuristic,
        "avg_judge": r.avg_judge,
        "avg_latency_ms": r.avg_latency_ms,
        "error": r.error,
        "results": r.results,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }
