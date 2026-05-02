# Atlas Dashboard — Overview Page Improvement Plan

**Date:** 2026-04-30  
**Status:** Ready for implementation  
**Estimated effort:** 1 engineering session (~3–4 hours)

---

## 0. Root Cause

Every KPI on the Overview page comes from `metrics.snapshot()` — in-process memory that
resets on every server restart.  The database has 5 days of real history that is never
surfaced:

| Fact (live DB today) | Value |
|---|---|
| Routing decisions | 33 rows |
| Usage records | 90 rows |
| Total cost | $0.5712 |
| Total tokens | 35,181 |
| Avg latency | 3,413 ms |
| Provider | claude-cli (100%) |
| Date range | 2026-04-25 → 2026-04-30 |

Three more data sources are invisible on the overview:
- **2-account Claude CLI pool** — neeraj + alpheric, both healthy
- **Distillation pipeline** — 27/100 chat samples, 18/100 code samples
- **Historical trend** — per-day cost and request volume

---

## 1. Implementation Order

1. Backend — augment `GET /admin/overview` (add `db_stats`, `pool_status`, `distillation_summary`)
2. Backend — add `GET /admin/analytics/daily-stats` (7-day daily rollup from DB)
3. Frontend — extend `types/index.ts` with new interfaces
4. Frontend — add `getDailyStats()` to `lib/api.ts`
5. Frontend — restructure `Overview.tsx` (5 rows, top-to-bottom)

---

## 2. Backend Changes

### File: `src/a1/dashboard/analytics_router.py`

#### 2.1 New imports needed at top

```python
from datetime import datetime, timedelta, timezone
from sqlalchemy import case, func, select
from a1.db.models import RoutingDecision
```

#### 2.2 Replace `GET /admin/overview`

Current version is 7 lines.  Replace entirely with the following.  Preserves all
existing return keys; adds `db_stats`, `pool_status`, `distillation_summary`.

```python
@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    from a1.dashboard.router import _live_connections

    conv_repo = ConversationRepo(db)
    conv_count = await conv_repo.count()
    providers = provider_registry.list_providers()

    # DB aggregate stats (all-time)
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
        select(RoutingDecision.provider, func.count(RoutingDecision.id).label("cnt"))
        .group_by(RoutingDecision.provider)
    )
    provider_counts_db = {row.provider: row.cnt for row in prov_dist}

    model_dist = await db.execute(
        select(RoutingDecision.model, func.count(RoutingDecision.id).label("cnt"))
        .group_by(RoutingDecision.model)
    )
    model_counts_db = {row.model: row.cnt for row in model_dist}

    local_dist = await db.execute(
        select(RoutingDecision.is_local, func.count(RoutingDecision.id).label("cnt"))
        .group_by(RoutingDecision.is_local)
    )
    local_map   = {row.is_local: row.cnt for row in local_dist}
    local_count = local_map.get(True, 0)
    total_reqs  = int(agg_row.total_requests or 0)
    local_pct   = round((local_count / total_reqs * 100) if total_reqs > 0 else 0, 1)

    recent_result = await db.execute(
        select(RoutingDecision).order_by(RoutingDecision.created_at.desc()).limit(20)
    )
    recent_decisions = recent_result.scalars().all()

    db_stats = {
        "total_requests": total_reqs,
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

    # Claude CLI pool status
    pool_status = []
    try:
        from a1.providers.claude_cli import ClaudeCLIPool
        cli_obj = provider_registry._providers.get("claude-cli")
        if isinstance(cli_obj, ClaudeCLIPool):
            pool_status = cli_obj.pool_status()
    except Exception:
        pass

    # Distillation summary
    distillation_summary = {"enabled": False, "min_samples": 100, "task_types": []}
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
                    "ready_for_training": tt.claude_sample_count >= _settings.distillation_min_samples,
                    "remaining": max(0, _settings.distillation_min_samples - tt.claude_sample_count),
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
```

**Note on `provider_registry._providers`:** Check `src/a1/providers/registry.py` for the
exact dict name.  If a public `get_provider(name)` method exists, use it instead.

---

#### 2.3 New `GET /admin/analytics/daily-stats`

Add after the existing `recent_requests` endpoint.  `func.date()` works on both
SQLite and PostgreSQL.

```python
@router.get("/analytics/daily-stats")
async def daily_stats(
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
):
    """Last N days of daily aggregated stats from routing_decisions table."""
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
```

---

## 3. Frontend Changes

### `dashboard-ui/src/types/index.ts`

Add the following new interfaces.  Replace the existing `OverviewData` interface.

```typescript
export interface DbRecentRequest {
  id: string; provider: string; model: string; task_type: string | null;
  strategy: string; latency_ms: number; cost_usd: number;
  prompt_tokens: number; completion_tokens: number;
  is_local: boolean; cache_hit: boolean; error: string | null; created_at: string | null;
}

export interface DbStats {
  total_requests: number; total_cost_usd: number;
  total_prompt_tokens: number; total_completion_tokens: number;
  avg_latency_ms: number; error_count: number;
  local_count: number; external_count: number; local_pct: number;
  provider_counts: Record<string, number>;
  model_counts: Record<string, number>;
  recent_requests: DbRecentRequest[];
}

export interface PoolAccountStatus {
  user: string; healthy: boolean; cli_path: string;
  sessions: number; requests: number;
  input_tokens: number; output_tokens: number; cost_usd: number;
}

export interface DistillationTaskType {
  task_type: string; claude_samples: number; training_threshold: number;
  local_handoff_pct: number; ready_for_training: boolean; remaining: number;
}

export interface DistillationSummary {
  enabled: boolean; min_samples: number; task_types: DistillationTaskType[];
}

export interface DailyStatPoint {
  day: string; requests: number; cost_usd: number;
  prompt_tokens: number; completion_tokens: number;
  total_tokens: number; avg_latency_ms: number;
}

// Replace existing OverviewData:
export interface OverviewData {
  metrics: MetricsSnapshot;
  conversations_count: number;
  providers: Provider[];
  active_connections: number;
  db_stats: DbStats;
  pool_status: PoolAccountStatus[];
  distillation_summary: DistillationSummary;
}
```

---

### `dashboard-ui/src/lib/api.ts`

```typescript
export const getDailyStats = (days = 7) =>
  api.get('/admin/analytics/daily-stats', { params: { days } }).then((r) => r.data);
```

---

### `dashboard-ui/src/components/shared/StatsCard.tsx`

Add optional `live?: boolean` prop.  When true, renders a pulsing `<Badge dot status="processing" />`
in the top-right corner of the card.

---

### `dashboard-ui/src/pages/Overview.tsx`

#### Replace 4 useQuery calls with 2

```typescript
const { data, isLoading } = useQuery<OverviewData>({
  queryKey: ['overview'],
  queryFn: getOverview,
  refetchInterval: 5_000,          // primary — 5s
});

const { data: dailyStats = [] } = useQuery<DailyStatPoint[]>({
  queryKey: ['dailyStats'],
  queryFn: async () => { const r = await getDailyStats(7); return r.data ?? []; },
  refetchInterval: 60_000,         // historical — 60s
});
```

Remove: `leaderboard`, `recentReqs`, `tokenSeries` queries.

#### Derived values

```typescript
const m  = data?.metrics;
const db = data?.db_stats;
const hasLiveData = (m?.request_count ?? 0) > 0;
```

---

#### Row 1 — Hero bar

Keep gradient card.  Replace content:
- Left: provider count, model count
- Right tags: `local_pct %`, `$total_cost`, `total_tokens tokens`, `N/N CLI`
- **Remove:** uptime counter, old token in/out tags

---

#### Row 2 — 8 KPI cards (source = `db_stats`)

| Card | Value | Live badge |
|---|---|---|
| Requests | `db.total_requests` | yes |
| Total Cost | `$db.total_cost_usd` | yes |
| Avg Latency | `db.avg_latency_ms ms` | yes |
| Conversations | `data.conversations_count` | no |
| Local % | `db.local_pct %` | no |
| Tokens | `prompt + completion` | yes |
| Errors | `db.error_count` | no |
| CLI Pool | `healthy/total` | no |

---

#### Row 3 — Daily trend (14/24) + CLI Pool (10/24)

**Daily trend:** `BarChart` dual-axis, `requests` left (blue) + `cost_usd` right (amber).
X labels: `day.slice(5)` → `"04-25"`.  Height 220px.

**CLI Pool:** inner card per account — name (monospace), health badge, sessions,
requests, tokens in/out, cost.

---

#### Row 4 — Distillation (10/24) + Recent Requests (14/24)

**Distillation:** `Progress` bar per task type.
- Blue until ready, green when `ready_for_training`.
- Sub-text: `"{remaining} more to trigger training"` or `"{handoff_pct}% routed locally"`.

**Recent Requests:** `List` from `db.recent_requests` (20 rows from DB):
- `LOCAL` green / `CACHE` purple / `EXT` blue tag
- Model name (monospace, ellipsis)
- `prompt→completion` tokens
- Latency (red > LATENCY_WARN_MS, green otherwise)
- `$cost` right-aligned

---

#### Row 5 — Provider pie (8/24) + Infrastructure Health (16/24)

**Provider pie:** source = `db.provider_counts` (DB, not in-memory).

**Infrastructure:** existing provider cards.  Inside `claude-cli` card, show inline
per-account list (user, badge, request count).

---

#### What is removed

| Removed | Reason |
|---|---|
| Local / External / Savings row | Savings comparison is arbitrary with 0% local routing |
| Uptime counter | Resets on restart; misleading |
| Token timeseries area chart | Always empty after restart; replaced by daily bar chart |
| `leaderboard` / `recentReqs` / `tokenSeries` queries | Data embedded in overview or replaced |

---

## 4. Data Flow

```
Component                  Source                               Refresh
───────────────────────────────────────────────────────────────────────
Row 1  Hero                data.db_stats                        5s
Row 2  KPIs                data.db_stats + conversations_count  5s
       Live badge          metrics.request_count > 0            5s
Row 3  Daily chart         dailyStats (new endpoint)            60s
Row 3  Pool card           data.pool_status                     5s
Row 4  Distillation        data.distillation_summary            5s
Row 4  Recent requests     data.db_stats.recent_requests        5s
Row 5  Provider pie        data.db_stats.provider_counts        5s
Row 5  Infrastructure      data.providers + data.pool_status    5s
```

---

## 5. Testing Checklist

- [ ] Overview loads without errors after a fresh server restart
- [ ] KPI "Requests" = 33
- [ ] KPI "Total Cost" = $0.5712
- [ ] KPI "Avg Latency" ≈ 3413 ms
- [ ] Daily chart shows 5–6 bars (Apr 25–30)
- [ ] Pool card shows neeraj (healthy) and alpheric (healthy)
- [ ] Distillation shows chat 27/100, code 18/100
- [ ] Recent requests shows 20 rows with model, latency, cost, direction
- [ ] Provider pie shows claude-cli: 33 (100%)
- [ ] Infrastructure claude-cli card shows both accounts inline
- [ ] "Live" badge appears on KPI cards after one proxy request
- [ ] dailyStats fires once on load, then every 60s (not 5s)
- [ ] npm run build passes with no TypeScript errors
