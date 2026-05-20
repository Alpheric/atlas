"""Analytics & metrics endpoints.

Endpoints:
  GET  /overview
  GET  /metrics
  GET  /analytics/token-timeseries
  GET  /analytics/cost-timeseries
  GET  /analytics/request-heatmap
  GET  /analytics/model-leaderboard
  GET  /analytics/recent-requests
  GET  /analytics/local-vs-external
  GET  /analytics/latency
  GET  /analytics/errors
  POST /models/compare
  GET  /routing/decisions
  GET  /routing/performance
  GET  /analytics/cost-by-workspace
  GET  /analytics/cost-by-key
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.metrics import metrics
from a1.db.models import RoutingDecision
from a1.db.repositories import ConversationRepo, RoutingRepo
from a1.dependencies import get_db
from a1.providers.registry import provider_registry

router = APIRouter()


# --- Overview ---
@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    from a1.dashboard.router import _live_connections

    conv_repo = ConversationRepo(db)
    conv_count = await conv_repo.count()
    providers = provider_registry.list_providers()

    # ── DB aggregate stats (all-time, survives restarts) ──────────────────
    agg = await db.execute(
        select(
            func.count(RoutingDecision.id).label("total_requests"),
            func.sum(RoutingDecision.cost_usd).label("total_cost"),
            func.sum(RoutingDecision.prompt_tokens).label("total_prompt_tokens"),
            func.sum(RoutingDecision.completion_tokens).label("total_completion_tokens"),
            func.avg(RoutingDecision.latency_ms).label("avg_latency_ms"),
            func.sum(case((RoutingDecision.error.isnot(None), 1), else_=0)).label("error_count"),
        )
    )
    agg_row = agg.one()

    prov_dist = await db.execute(
        select(RoutingDecision.provider, func.count(RoutingDecision.id).label("cnt")).group_by(
            RoutingDecision.provider
        )
    )
    provider_counts_db = {row.provider: row.cnt for row in prov_dist}

    model_dist = await db.execute(
        select(RoutingDecision.model, func.count(RoutingDecision.id).label("cnt")).group_by(
            RoutingDecision.model
        )
    )
    model_counts_db = {row.model: row.cnt for row in model_dist}

    local_dist = await db.execute(
        select(RoutingDecision.is_local, func.count(RoutingDecision.id).label("cnt")).group_by(
            RoutingDecision.is_local
        )
    )
    local_map = {row.is_local: row.cnt for row in local_dist}
    local_count = local_map.get(True, 0)
    total_reqs = int(agg_row.total_requests or 0)
    local_pct = round((local_count / total_reqs * 100) if total_reqs > 0 else 0, 1)

    recent_result = await db.execute(
        select(RoutingDecision).order_by(RoutingDecision.created_at.desc()).limit(20)
    )
    recent_decisions = recent_result.scalars().all()

    # Count self-healed responses (new self-heal column)
    healed_result = await db.execute(
        select(func.count(RoutingDecision.id)).where(RoutingDecision.self_healed.is_(True))
    )
    self_healed_count = int(healed_result.scalar() or 0)

    db_stats = {
        "total_requests": total_reqs,
        "self_healed_count": self_healed_count,
        "total_cost_usd": float(agg_row.total_cost or 0),
        "total_prompt_tokens": int(agg_row.total_prompt_tokens or 0),
        "total_completion_tokens": int(agg_row.total_completion_tokens or 0),
        "avg_latency_ms": round(float(agg_row.avg_latency_ms or 0), 1),
        "error_count": int(agg_row.error_count or 0),
        "local_count": local_count,
        "external_count": local_map.get(False, 0),
        "local_pct": local_pct,
        "provider_counts": provider_counts_db,
        "model_counts": model_counts_db,
        "recent_requests": [
            {
                "id": str(d.id),
                "provider": d.provider,
                "model": d.model,
                "task_type": d.task_type,
                "strategy": d.strategy,
                "latency_ms": d.latency_ms,
                "cost_usd": float(d.cost_usd),
                "prompt_tokens": d.prompt_tokens,
                "completion_tokens": d.completion_tokens,
                "is_local": d.is_local,
                "cache_hit": d.cache_hit,
                "error": d.error,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in recent_decisions
        ],
    }

    # ── Claude CLI pool status ─────────────────────────────────────────────
    pool_status = []
    try:
        from a1.providers.claude_cli import ClaudeCLIPool

        cli_obj = provider_registry.get_provider("claude-cli")
        if isinstance(cli_obj, ClaudeCLIPool):
            pool_status = cli_obj.pool_status()
    except Exception:
        pass

    # ── Distillation summary ──────────────────────────────────────────────
    distillation_summary: dict = {"enabled": False, "min_samples": 100, "task_types": []}
    try:
        from a1.db.repositories import TaskTypeReadinessRepo
        from config.settings import settings as _settings

        readiness_repo = TaskTypeReadinessRepo(db)
        task_types = await readiness_repo.list_all()
        distillation_summary = {
            "enabled": bool(_settings.distillation_enabled),
            "min_samples": _settings.distillation_min_samples,
            "task_types": [
                {
                    "task_type": tt.task_type,
                    "claude_samples": tt.claude_sample_count,
                    "training_threshold": _settings.distillation_min_samples,
                    "local_handoff_pct": round(float(tt.local_handoff_pct or 0) * 100, 1),
                    "ready_for_training": tt.claude_sample_count
                    >= _settings.distillation_min_samples,  # noqa: E501
                    "remaining": max(
                        0, _settings.distillation_min_samples - tt.claude_sample_count
                    ),  # noqa: E501
                }
                for tt in task_types
            ],
        }
    except Exception:
        pass

    return {
        "metrics": metrics.snapshot(),
        "conversations_count": conv_count,
        "providers": providers,
        "active_connections": len(_live_connections),
        "db_stats": db_stats,
        "pool_status": pool_status,
        "distillation_summary": distillation_summary,
    }


# --- Metrics ---
@router.get("/metrics")
async def get_metrics():
    return metrics.snapshot()


# --- Enhanced Analytics ---
@router.get("/analytics/token-timeseries")
async def token_timeseries():
    """Token usage over time (per-minute buckets)."""
    return {"data": metrics.token_timeseries()}


@router.get("/analytics/cost-timeseries")
async def cost_timeseries():
    """Cost trend over time (per-minute buckets)."""
    return {"data": metrics.cost_timeseries()}


@router.get("/analytics/request-heatmap")
async def request_heatmap():
    """Request volume heatmap by day-of-week and hour."""
    return {"data": metrics.request_heatmap()}


@router.get("/analytics/model-leaderboard")
async def model_leaderboard():
    """Model performance leaderboard with detailed stats."""
    return {"data": metrics.model_leaderboard()}


@router.get("/analytics/recent-requests")
async def recent_requests(limit: int = 50):
    """Recent request history for live feed."""
    return {"data": metrics.recent_requests(limit=limit)}


@router.get("/analytics/daily-stats")
async def daily_stats(
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
):
    """Last N days of daily aggregated stats from routing_decisions (DB-backed)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = await db.execute(
        select(
            func.date(RoutingDecision.created_at).label("day"),
            func.count(RoutingDecision.id).label("requests"),
            func.sum(RoutingDecision.cost_usd).label("cost_usd"),
            func.sum(RoutingDecision.prompt_tokens).label("prompt_tokens"),
            func.sum(RoutingDecision.completion_tokens).label("completion_tokens"),
            func.avg(RoutingDecision.latency_ms).label("avg_latency_ms"),
        )
        .where(RoutingDecision.created_at >= cutoff)
        .group_by(func.date(RoutingDecision.created_at))
        .order_by(func.date(RoutingDecision.created_at).asc())
    )

    return {
        "days": days,
        "data": [
            {
                "day": str(row.day),
                "requests": row.requests,
                "cost_usd": round(float(row.cost_usd or 0), 6),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "total_tokens": int((row.prompt_tokens or 0) + (row.completion_tokens or 0)),
                "avg_latency_ms": round(float(row.avg_latency_ms or 0), 1),
            }
            for row in rows
        ],
    }


@router.get("/analytics/local-vs-external")
async def analytics_local_vs_external():
    snapshot = metrics.snapshot()
    return {
        "local": snapshot["local"],
        "external": snapshot["external"],
        "savings_usd": snapshot["savings_usd"],
    }


@router.get("/analytics/latency")
async def analytics_latency():
    snapshot = metrics.snapshot()
    result = []
    for model in snapshot.get("model_counts", {}):
        percs = metrics.get_latency_percentiles(model)
        result.append({"model": model, **percs})
    return {"data": result}


@router.get("/analytics/errors")
async def analytics_errors():
    snapshot = metrics.snapshot()
    return {"data": snapshot.get("error_counts_by_provider", {})}


# --- Routing ---
@router.get("/routing/decisions")
async def routing_decisions(
    limit: int = Query(100, le=500),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    task_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    repo = RoutingRepo(db)
    decisions = await repo.list_recent(
        limit=limit, date_from=date_from, date_to=date_to, task_type=task_type
    )
    return {
        "data": [
            {
                "id": str(d.id),
                "provider": d.provider,
                "model": d.model,
                "task_type": d.task_type,
                "strategy": d.strategy,
                "confidence": d.confidence,
                "latency_ms": d.latency_ms,
                "cost_usd": float(d.cost_usd),
                "prompt_tokens": d.prompt_tokens,
                "completion_tokens": d.completion_tokens,
                "is_local": d.is_local,
                "error": d.error,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in decisions
        ]
    }


@router.get("/routing/performance")
async def routing_performance(
    task_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    repo = RoutingRepo(db)
    perf = await repo.get_performance(task_type=task_type)
    return {
        "data": [
            {
                "task_type": p.task_type,
                "provider": p.provider,
                "model": p.model,
                "avg_quality": p.avg_quality,
                "avg_latency_ms": p.avg_latency_ms,
                "avg_cost_usd": p.avg_cost_usd,
                "sample_count": p.sample_count,
            }
            for p in perf
        ]
    }


# --- Model Comparison ---
@router.post("/models/compare")
async def compare_models(
    prompt: str,
    models: list[str],
    max_tokens: int = 500,
):
    """Run the same prompt through multiple models and compare responses."""
    import time

    from a1.proxy.request_models import ChatCompletionRequest, MessageInput

    results = []
    for model_name in models:
        provider = provider_registry.get_provider_for_model(model_name)
        if not provider:
            results.append({"model": model_name, "error": "No provider found"})
            continue
        try:
            req = ChatCompletionRequest(
                model=model_name,
                messages=[MessageInput(role="user", content=prompt)],
                max_tokens=max_tokens,
            )
            start = time.time()
            resp = await provider.complete(req)
            latency = int((time.time() - start) * 1000)
            results.append(
                {
                    "model": model_name,
                    "provider": provider.name,
                    "content": resp.choices[0].message.content if resp.choices else "",
                    "latency_ms": latency,
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                }
            )
        except Exception as e:
            results.append({"model": model_name, "error": str(e)})

    return {"results": results}


# ---------------------------------------------------------------------------
# Web Search Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/search/overview")
async def search_overview():
    """In-memory web search statistics: counts, latency, provider breakdown."""
    return metrics.web_search_snapshot()


@router.get("/analytics/search/recent")
async def search_recent(limit: int = Query(50, ge=1, le=200)):
    """Live feed of recent web searches (in-memory, resets on restart)."""
    return {"searches": metrics.recent_searches(limit=limit)}


@router.get("/analytics/search/history")
async def search_history(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """DB-backed search history with per-day rollup.

    Returns daily counts, average latency, provider usage, and block rate.
    """

    from sqlalchemy import func, select

    from a1.common.tz import now_ist
    from a1.db.models import WebSearchRun

    since = now_ist() - timedelta(days=days)
    result = await db.execute(
        select(
            func.date(WebSearchRun.created_at).label("day"),
            func.count(WebSearchRun.id).label("total"),
            func.sum(case((WebSearchRun.blocked == False, 1), else_=0)).label("succeeded"),  # noqa: E712
            func.sum(case((WebSearchRun.blocked == True, 1), else_=0)).label("blocked"),  # noqa: E712
            func.avg(WebSearchRun.latency_ms).label("avg_latency_ms"),
            WebSearchRun.provider,
        )
        .where(WebSearchRun.created_at >= since)
        .group_by(func.date(WebSearchRun.created_at), WebSearchRun.provider)
        .order_by(func.date(WebSearchRun.created_at).desc())
    )
    rows = result.all()
    return {
        "days": days,
        "data": [
            {
                "day": str(r.day),
                "total": r.total,
                "succeeded": r.succeeded or 0,
                "blocked": r.blocked or 0,
                "avg_latency_ms": round(float(r.avg_latency_ms or 0), 1),
                "provider": r.provider,
            }
            for r in rows
        ],
    }


@router.get("/analytics/search/providers")
async def search_providers():
    """Current status of all registered search providers."""
    from a1.search.providers.registry import search_registry

    return {
        "available": search_registry.is_available(),
        "active": (
            search_registry.active_provider.name if search_registry.active_provider else None
        ),
        "providers": search_registry.status(),
    }


@router.get("/analytics/search/citations")
async def search_citations(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Recent citation records from web-grounded answers."""
    from a1.db.models import WebCitation

    result = await db.execute(
        select(WebCitation).order_by(WebCitation.accessed_at.desc()).limit(limit)
    )
    citations = result.scalars().all()
    return {
        "data": [
            {
                "id": str(c.id),
                "run_id": str(c.run_id),
                "source_url": c.source_url,
                "title": c.title,
                "published_date": c.published_date,
                "accessed_at": c.accessed_at.isoformat(),
                "claim_supported": c.claim_supported,
                "rank": c.rank,
            }
            for c in citations
        ],
        "total": len(citations),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cost attribution (Phase 2.2) — per-workspace and per-key spend + budget burn
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/analytics/cost-by-workspace")
async def cost_by_workspace(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Per-workspace spend over the window, with monthly budget burn-down.

    Joins UsageRecord → Workspace (name) and WorkspaceBudget (limit / current
    month spend). Rows with no workspace_id are grouped under "(unattributed)".
    """
    from a1.db.models import UsageRecord, Workspace, WorkspaceBudget

    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        await db.execute(
            select(
                UsageRecord.workspace_id,
                func.count().label("requests"),
                func.coalesce(func.sum(UsageRecord.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(UsageRecord.completion_tokens), 0).label(
                    "completion_tokens"
                ),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost_usd"),
                func.coalesce(
                    func.sum(UsageRecord.equivalent_external_cost_usd), 0
                ).label("equivalent_external_cost_usd"),
                func.coalesce(
                    func.sum(case((UsageRecord.error.is_(True), 1), else_=0)), 0
                ).label("errors"),
            )
            .where(UsageRecord.created_at >= since)
            .group_by(UsageRecord.workspace_id)
        )
    ).all()

    # Resolve workspace names + budgets
    names: dict[str, str] = {}
    ws_result = await db.execute(select(Workspace.id, Workspace.name))
    for wid, wname in ws_result.all():
        names[str(wid)] = wname

    budgets: dict[str, dict] = {}
    bud_result = await db.execute(
        select(
            WorkspaceBudget.workspace_id,
            WorkspaceBudget.monthly_limit_usd,
            WorkspaceBudget.current_month_usd,
            WorkspaceBudget.alert_threshold_pct,
            WorkspaceBudget.budget_month,
        )
    )
    for wid, limit, cur, thr, month in bud_result.all():
        budgets[str(wid)] = {
            "monthly_limit_usd": float(limit or 0),
            "current_month_usd": float(cur or 0),
            "alert_threshold_pct": float(thr or 0.8),
            "budget_month": month,
            "pct_used": round(float(cur or 0) / float(limit), 4) if limit else None,
        }

    data = []
    for r in rows:
        wid = str(r.workspace_id) if r.workspace_id else None
        data.append(
            {
                "workspace_id": wid,
                "workspace_name": names.get(wid, "(unattributed)") if wid else "(unattributed)",
                "requests": int(r.requests),
                "prompt_tokens": int(r.prompt_tokens),
                "completion_tokens": int(r.completion_tokens),
                "total_tokens": int(r.prompt_tokens) + int(r.completion_tokens),
                "cost_usd": round(float(r.cost_usd), 6),
                "savings_usd": round(float(r.equivalent_external_cost_usd), 6),
                "errors": int(r.errors),
                "budget": budgets.get(wid) if wid else None,
            }
        )

    data.sort(key=lambda d: d["cost_usd"], reverse=True)
    return {
        "window_days": days,
        "data": data,
        "total_cost_usd": round(sum(d["cost_usd"] for d in data), 6),
    }


@router.get("/analytics/cost-by-key")
async def cost_by_key(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Per-API-key spend over the window. Labels keys via ApiKey.name when
    available, else AtlasApiKey tenant info, else the truncated hash."""
    from a1.db.models import ApiKey, AtlasApiKey, UsageRecord

    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        await db.execute(
            select(
                UsageRecord.api_key_hash,
                func.count().label("requests"),
                func.coalesce(func.sum(UsageRecord.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(UsageRecord.completion_tokens), 0).label(
                    "completion_tokens"
                ),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost_usd"),
                func.coalesce(
                    func.sum(case((UsageRecord.error.is_(True), 1), else_=0)), 0
                ).label("errors"),
            )
            .where(UsageRecord.created_at >= since)
            .group_by(UsageRecord.api_key_hash)
            .order_by(func.sum(UsageRecord.cost_usd).desc())
            .limit(limit)
        )
    ).all()

    hashes = [r.api_key_hash for r in rows if r.api_key_hash]

    # Build label maps
    dash_names: dict[str, str] = {}
    if hashes:
        dr = await db.execute(
            select(ApiKey.key_hash, ApiKey.name).where(ApiKey.key_hash.in_(hashes))
        )
        for kh, nm in dr.all():
            if nm:
                dash_names[kh] = nm

    tenant_names: dict[str, str] = {}
    if hashes:
        tr = await db.execute(
            select(AtlasApiKey.key_hash, AtlasApiKey.tenant_name, AtlasApiKey.source).where(
                AtlasApiKey.key_hash.in_(hashes)
            )
        )
        for kh, tn, src in tr.all():
            tenant_names[kh] = tn or f"({src})"

    data = []
    for r in rows:
        kh = r.api_key_hash
        label = (
            dash_names.get(kh)
            or tenant_names.get(kh)
            or (f"{kh[:12]}…" if kh else "(no key)")
        )
        data.append(
            {
                "api_key_hash": kh,
                "label": label,
                "requests": int(r.requests),
                "prompt_tokens": int(r.prompt_tokens),
                "completion_tokens": int(r.completion_tokens),
                "total_tokens": int(r.prompt_tokens) + int(r.completion_tokens),
                "cost_usd": round(float(r.cost_usd), 6),
                "errors": int(r.errors),
            }
        )

    return {
        "window_days": days,
        "data": data,
        "total_cost_usd": round(sum(d["cost_usd"] for d in data), 6),
    }
