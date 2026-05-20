"""Routing replay / shadow eval (Phase 3.2).

Re-runs historical routing decisions through a *candidate* routing policy and
projects the cost difference vs what actually happened — so a routing change can
be measured before it ships, instead of guessing.

The candidate policy is a simple per-task-type model override map, e.g.
  {"code": "qwen2.5-coder:7b", "*": "gemini-2.5-flash"}
"*" is the catch-all. Any task type not in the map keeps its actual model.

Projected cost uses the *recorded* token counts from each historical
RoutingDecision multiplied by the candidate model's per-token rates (from
config/providers.yaml). Local models are treated as $0 (self-hosted). This is a
cost/route-shift projection; true quality requires the eval system (Phase 2.3),
which this intentionally does not fake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from a1.common.logging import get_logger

log = get_logger("routing.replay")

_cost_table: dict[str, tuple[float, float]] | None = None  # model -> (in_per_1k, out_per_1k)
_local_models: set[str] = set()


def _load_cost_table() -> dict[str, tuple[float, float]]:
    """model name -> (cost_per_1k_input, cost_per_1k_output) from providers.yaml.
    Local (ollama) models are recorded with 0 cost and tracked as local."""
    global _cost_table
    if _cost_table is not None:
        return _cost_table

    table: dict[str, tuple[float, float]] = {}
    try:
        import os

        path = os.path.join(os.getcwd(), "config", "providers.yaml")
        with open(path) as f:
            data = yaml.safe_load(f)
        for prov_name, prov in (data.get("providers", {}) or {}).items():
            for m in prov.get("models", []) or []:
                name = m.get("name")
                if not name:
                    continue
                cin = float(m.get("cost_per_1k_input", 0) or 0)
                cout = float(m.get("cost_per_1k_output", 0) or 0)
                table[name] = (cin, cout)
                if prov_name == "ollama":
                    _local_models.add(name)
    except Exception as e:
        log.warning(f"[replay] could not load cost table: {e}")

    _cost_table = table
    return table


def _is_local_model(model: str) -> bool:
    """True if the model is served by Ollama (local, $0). Ollama models are
    auto-discovered (not in providers.yaml), so consult the live registry and
    fall back to the tag-format heuristic."""
    if model in _local_models:
        return True
    try:
        from a1.providers.registry import provider_registry

        ol = provider_registry.get_provider("ollama")
        if ol and any(m.name == model for m in ol.list_models()):
            _local_models.add(model)
            return True
    except Exception:
        pass
    # Heuristic: ollama tags look like "name:tag" and aren't in the external
    # cost table (which uses bare names like gpt-4o, gemini-2.5-pro).
    table = _load_cost_table()
    if ":" in model and model not in table:
        return True
    return False


def _project_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Projected cost for a model + token counts. None if rates unknown.
    Local models (ollama) project to $0."""
    table = _load_cost_table()
    if _is_local_model(model):
        return 0.0
    rates = table.get(model)
    if rates is None:
        # Prefix match (e.g. qwen2.5-coder:7b-q4 → qwen2.5-coder:7b)
        for k, v in table.items():
            if model.startswith(k.split(":")[0]):
                rates = v
                break
    if rates is None:
        return None
    cin, cout = rates
    return (prompt_tokens / 1000.0) * cin + (completion_tokens / 1000.0) * cout


async def replay_routing(
    candidate: dict[str, str],
    days: int = 7,
    limit: int = 5000,
) -> dict:
    """Replay recent RoutingDecisions under a candidate policy.

    candidate: {task_type|"*": model}. Returns actual vs projected cost, the
    number of routes that would change, local-vs-external shift, and a per-task
    breakdown.
    """
    from sqlalchemy import select

    from a1.db.engine import async_session
    from a1.db.models import RoutingDecision

    since = datetime.now(timezone.utc) - timedelta(days=days)
    _load_cost_table()

    async with async_session() as db:
        rows = (
            await db.execute(
                select(RoutingDecision)
                .where(RoutingDecision.created_at >= since)
                .order_by(RoutingDecision.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

    actual_cost = 0.0
    projected_cost = 0.0
    changed = 0
    unknown_rate = 0
    actual_local = 0
    projected_local = 0
    per_task: dict[str, dict] = {}

    for r in rows:
        task = r.task_type or "unknown"
        actual_model = r.model
        cand_model = candidate.get(task) or candidate.get("*") or actual_model

        a_cost = float(r.cost_usd or 0)
        actual_cost += a_cost
        if r.is_local:
            actual_local += 1

        p_cost = _project_cost(cand_model, r.prompt_tokens or 0, r.completion_tokens or 0)
        if p_cost is None:
            unknown_rate += 1
            p_cost = a_cost  # fall back to actual when rates unknown
        projected_cost += p_cost
        if _is_local_model(cand_model):
            projected_local += 1
        if cand_model != actual_model:
            changed += 1

        pt = per_task.setdefault(
            task,
            {
                "requests": 0,
                "actual_cost": 0.0,
                "projected_cost": 0.0,
                "candidate_model": cand_model,
            },
        )
        pt["requests"] += 1
        pt["actual_cost"] += a_cost
        pt["projected_cost"] += p_cost

    for pt in per_task.values():
        pt["actual_cost"] = round(pt["actual_cost"], 6)
        pt["projected_cost"] = round(pt["projected_cost"], 6)
        pt["delta"] = round(pt["projected_cost"] - pt["actual_cost"], 6)

    n = len(rows)
    return {
        "window_days": days,
        "candidate": candidate,
        "decisions_evaluated": n,
        "routes_changed": changed,
        "routes_changed_pct": round(changed / n, 4) if n else 0,
        "unknown_rate_count": unknown_rate,
        "actual_cost_usd": round(actual_cost, 6),
        "projected_cost_usd": round(projected_cost, 6),
        "projected_delta_usd": round(projected_cost - actual_cost, 6),
        "projected_savings_usd": round(actual_cost - projected_cost, 6),
        "actual_local_count": actual_local,
        "projected_local_count": projected_local,
        "per_task": per_task,
        "note": "Cost projection only — quality not simulated. Use eval runs to measure quality.",
    }
