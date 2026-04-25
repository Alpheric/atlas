"""Governance admin endpoints: model registry, approvals, audit log, budgets.

Endpoints:
  GET/POST  /model-versions          -- list / create model versions
  GET       /model-versions/{id}     -- detail
  POST      /model-versions/{id}/promote  -- promote draft->staging->active
  POST      /model-versions/{id}/retire   -- retire
  GET/POST  /approvals               -- list / create approval requests
  POST      /approvals/{id}/approve  -- approve
  POST      /approvals/{id}/reject   -- reject
  GET       /audit-log               -- list audit events
  GET/POST  /budgets                 -- list / create workspace budgets
"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.logging import get_logger
from a1.common.tz import now_ist
from a1.dependencies import get_db

log = get_logger("dashboard.governance")
router = APIRouter()


# ---------------------------------------------------------------------------
# Audit helper (used by other sub-routers too)
# ---------------------------------------------------------------------------


async def record_audit(
    db: AsyncSession | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    api_key_hash: str | None = None,
    action: str = "",
    entity_type: str = "",
    entity_id: str | None = None,
    details: dict | None = None,
):
    """Record an audit event. Can be called from any endpoint."""
    try:
        from a1.db.models import AuditEvent

        if db is None:
            from a1.db.engine import async_session

            async with async_session() as session:
                async with session.begin():
                    event = AuditEvent(
                        workspace_id=_uuid.UUID(workspace_id) if workspace_id else None,
                        user_id=user_id,
                        api_key_hash=api_key_hash,
                        action=action,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        details=details or {},
                    )
                    session.add(event)
        else:
            from a1.db.models import AuditEvent

            event = AuditEvent(
                workspace_id=_uuid.UUID(workspace_id) if workspace_id else None,
                user_id=user_id,
                api_key_hash=api_key_hash,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details or {},
            )
            db.add(event)
            await db.flush()
    except Exception as e:
        log.debug(f"Failed to record audit event: {e}")


# ---------------------------------------------------------------------------
# Model Versions
# ---------------------------------------------------------------------------


@router.get("/model-versions")
async def list_model_versions(
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from a1.db.models import ModelVersion

    stmt = select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(50)
    if task_type:
        stmt = stmt.where(ModelVersion.task_type == task_type)
    if status:
        stmt = stmt.where(ModelVersion.status == status)
    result = await db.execute(stmt)
    versions = result.scalars().all()
    return {
        "data": [
            {
                "id": str(v.id),
                "task_type": v.task_type,
                "base_model": v.base_model,
                "adapter_path": v.adapter_path,
                "status": v.status,
                "version_tag": v.version_tag,
                "eval_scores": v.eval_scores,
                "created_at": v.created_at,
                "activated_at": v.activated_at,
            }
            for v in versions
        ]
    }


@router.post("/model-versions")
async def create_model_version(body: dict, db: AsyncSession = Depends(get_db)):
    from a1.db.models import ModelVersion

    mv = ModelVersion(
        id=_uuid.uuid4(),
        task_type=body.get("task_type", ""),
        base_model=body.get("base_model", ""),
        adapter_path=body.get("adapter_path"),
        training_run_id=_uuid.UUID(body["training_run_id"])
        if body.get("training_run_id")
        else None,
        eval_scores=body.get("eval_scores", {}),
        version_tag=body.get("version_tag"),
        created_by=body.get("created_by"),
    )
    db.add(mv)
    await db.flush()
    await record_audit(
        db,
        action="create",
        entity_type="model_version",
        entity_id=str(mv.id),
        details={"task_type": mv.task_type, "base_model": mv.base_model},
    )
    return {"id": str(mv.id), "status": "draft"}


@router.post("/model-versions/{version_id}/promote")
async def promote_model_version(version_id: str, db: AsyncSession = Depends(get_db)):
    """Promote: draft->staging (auto), staging->active (requires approval)."""
    from a1.db.models import ApprovalRequest, ModelVersion

    result = await db.execute(select(ModelVersion).where(ModelVersion.id == _uuid.UUID(version_id)))
    mv = result.scalar_one_or_none()
    if not mv:
        raise HTTPException(404, "Model version not found")

    if mv.status == "draft":
        mv.status = "staging"
        await record_audit(
            db,
            action="promote",
            entity_type="model_version",
            entity_id=version_id,
            details={"from": "draft", "to": "staging"},
        )
        return {"id": version_id, "status": "staging"}

    if mv.status == "staging":
        # Create approval request instead of auto-promoting
        approval = ApprovalRequest(
            id=_uuid.uuid4(),
            entity_type="model_version",
            entity_id=version_id,
            action="activate",
            details={"task_type": mv.task_type, "eval_scores": mv.eval_scores},
        )
        db.add(approval)
        await db.flush()
        await record_audit(
            db,
            action="request_approval",
            entity_type="model_version",
            entity_id=version_id,
            details={"approval_id": str(approval.id)},
        )
        return {
            "id": version_id,
            "status": "staging",
            "approval_id": str(approval.id),
            "message": "Approval required to activate. Use POST /admin/approvals/{id}/approve",
        }

    raise HTTPException(400, f"Cannot promote from status '{mv.status}'")


@router.post("/model-versions/{version_id}/retire")
async def retire_model_version(version_id: str, db: AsyncSession = Depends(get_db)):
    from a1.db.models import ModelVersion

    result = await db.execute(select(ModelVersion).where(ModelVersion.id == _uuid.UUID(version_id)))
    mv = result.scalar_one_or_none()
    if not mv:
        raise HTTPException(404, "Model version not found")
    mv.status = "retired"
    mv.retired_at = now_ist()
    await record_audit(db, action="retire", entity_type="model_version", entity_id=version_id)
    return {"id": version_id, "status": "retired"}


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


@router.get("/approvals")
async def list_approvals(
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from a1.db.models import ApprovalRequest

    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(50)
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    result = await db.execute(stmt)
    approvals = result.scalars().all()
    return {
        "data": [
            {
                "id": str(a.id),
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "action": a.action,
                "status": a.status,
                "reason": a.reason,
                "details": a.details,
                "created_at": a.created_at,
                "reviewed_at": a.reviewed_at,
            }
            for a in approvals
        ]
    }


@router.post("/approvals/{approval_id}/approve")
async def approve_request(
    approval_id: str, body: dict | None = None, db: AsyncSession = Depends(get_db)
):
    from a1.db.models import ApprovalRequest, ModelVersion

    body = body or {}
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == _uuid.UUID(approval_id))
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(404, "Approval request not found")
    if approval.status != "pending":
        raise HTTPException(400, f"Already {approval.status}")

    approval.status = "approved"
    approval.reviewed_by = body.get("reviewed_by")
    approval.reason = body.get("reason")
    approval.reviewed_at = now_ist()

    # Execute the approved action
    if approval.entity_type == "model_version" and approval.action == "activate":
        mv_result = await db.execute(
            select(ModelVersion).where(ModelVersion.id == _uuid.UUID(approval.entity_id))
        )
        mv = mv_result.scalar_one_or_none()
        if mv:
            mv.status = "active"
            mv.activated_at = now_ist()

    await record_audit(
        db,
        action="approve",
        entity_type="approval",
        entity_id=approval_id,
        details={"entity": approval.entity_type},
    )
    return {"id": approval_id, "status": "approved"}


@router.post("/approvals/{approval_id}/reject")
async def reject_request(
    approval_id: str, body: dict | None = None, db: AsyncSession = Depends(get_db)
):
    from a1.db.models import ApprovalRequest

    body = body or {}
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == _uuid.UUID(approval_id))
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(404, "Approval request not found")
    if approval.status != "pending":
        raise HTTPException(400, f"Already {approval.status}")

    approval.status = "rejected"
    approval.reviewed_by = body.get("reviewed_by")
    approval.reason = body.get("reason", "Rejected")
    approval.reviewed_at = now_ist()

    await record_audit(
        db,
        action="reject",
        entity_type="approval",
        entity_id=approval_id,
        details={"entity": approval.entity_type},
    )
    return {"id": approval_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


@router.get("/audit-log")
async def list_audit_events(
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    from a1.db.models import AuditEvent

    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    result = await db.execute(stmt)
    events = result.scalars().all()
    return {
        "data": [
            {
                "id": str(e.id),
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "user_id": e.user_id,
                "details": e.details,
                "created_at": e.created_at,
            }
            for e in events
        ]
    }


# ---------------------------------------------------------------------------
# Workspace Budgets
# ---------------------------------------------------------------------------


@router.get("/budgets")
async def list_budgets(db: AsyncSession = Depends(get_db)):
    from a1.db.models import WorkspaceBudget

    result = await db.execute(select(WorkspaceBudget))
    budgets = result.scalars().all()
    return {
        "data": [
            {
                "workspace_id": str(b.workspace_id),
                "monthly_limit_usd": float(b.monthly_limit_usd),
                "current_month_usd": float(b.current_month_usd),
                "alert_threshold_pct": b.alert_threshold_pct,
                "budget_month": b.budget_month,
                "updated_at": b.updated_at,
            }
            for b in budgets
        ]
    }


@router.post("/budgets")
async def set_budget(body: dict, db: AsyncSession = Depends(get_db)):
    from a1.db.models import WorkspaceBudget

    workspace_id = body.get("workspace_id")
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")

    from a1.common.tz import now_ist

    month = now_ist().strftime("%Y-%m")

    # Upsert
    result = await db.execute(
        select(WorkspaceBudget).where(WorkspaceBudget.workspace_id == _uuid.UUID(workspace_id))
    )
    budget = result.scalar_one_or_none()
    if budget:
        budget.monthly_limit_usd = body.get("monthly_limit_usd", budget.monthly_limit_usd)
        budget.alert_threshold_pct = body.get("alert_threshold_pct", budget.alert_threshold_pct)
        budget.budget_month = month
    else:
        budget = WorkspaceBudget(
            workspace_id=_uuid.UUID(workspace_id),
            monthly_limit_usd=body.get("monthly_limit_usd", 100.0),
            alert_threshold_pct=body.get("alert_threshold_pct", 0.8),
            budget_month=month,
        )
        db.add(budget)
    await db.flush()
    await record_audit(
        db,
        workspace_id=workspace_id,
        action="set_budget",
        entity_type="workspace_budget",
        entity_id=workspace_id,
        details={"monthly_limit_usd": float(budget.monthly_limit_usd)},
    )
    return {
        "workspace_id": workspace_id,
        "monthly_limit_usd": float(budget.monthly_limit_usd),
        "status": "set",
    }


# ---------------------------------------------------------------------------
# Compliance Summary
# ---------------------------------------------------------------------------

@router.get("/governance/compliance")
async def compliance_summary(db: AsyncSession = Depends(get_db)):
    """Return a compliance summary: approval stats, audit coverage, model governance."""
    from sqlalchemy import func, select
    from a1.db.models import ApprovalRequest, AuditEvent, ModelVersion

    # Approval stats
    approval_result = await db.execute(
        select(ApprovalRequest.status, func.count().label("count"))
        .group_by(ApprovalRequest.status)
    )
    approval_counts = {row.status: row.count for row in approval_result}

    # Audit event count (last 30 days)
    from a1.common.tz import now_ist
    import datetime
    since = now_ist() - datetime.timedelta(days=30)
    audit_result = await db.execute(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.created_at >= since)
    )
    audit_count = audit_result.scalar() or 0

    # Model version status breakdown
    mv_result = await db.execute(
        select(ModelVersion.status, func.count().label("count"))
        .group_by(ModelVersion.status)
    )
    mv_counts = {row.status: row.count for row in mv_result}

    return {
        "approvals": {
            "pending": approval_counts.get("pending", 0),
            "approved": approval_counts.get("approved", 0),
            "rejected": approval_counts.get("rejected", 0),
        },
        "audit_events_last_30d": audit_count,
        "model_versions": mv_counts,
        "compliance_score": 100 if approval_counts.get("pending", 0) == 0 else max(
            0, 100 - approval_counts.get("pending", 0) * 10
        ),
    }
