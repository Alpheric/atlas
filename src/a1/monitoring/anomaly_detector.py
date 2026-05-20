"""Anomaly detection + alerting (Phase 2.5).

An in-process background monitor that samples the live metrics each interval,
compares against a rolling EWMA baseline, and flags:

  - error_rate_spike : errors/requests in the interval exceeds a threshold
  - latency_spike    : interval avg latency > baseline * factor
  - cost_surge       : interval cost > baseline * factor
  - provider_down    : a provider that was healthy is now unhealthy

Anomalies are kept in a small in-memory ring (viewable via the dashboard) and,
if configured, POSTed to a Slack-compatible webhook ({"text": ...}). Everything
is best-effort — detection failures never affect serving.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field

import httpx

from a1.common.logging import get_logger
from a1.common.metrics import metrics
from a1.common.tz import now_ist
from a1.providers.registry import provider_registry
from config.settings import settings

log = get_logger("monitoring.anomaly")

_EWMA_ALPHA = 0.3  # weight of the newest interval in the rolling baseline


@dataclass
class Anomaly:
    kind: str
    severity: str  # "warning" | "critical"
    message: str
    value: float
    baseline: float
    detected_at: str


@dataclass
class _AnomalyState:
    anomalies: deque = field(default_factory=lambda: deque(maxlen=200))

    # Previous cumulative counters (to compute per-interval deltas)
    prev_requests: int = 0
    prev_errors: int = 0
    prev_cost: float = 0.0
    prev_latency_total: float = 0.0

    # EWMA baselines (per-interval)
    base_latency: float | None = None
    base_cost: float | None = None

    # Provider health from the last tick
    prev_health: dict = field(default_factory=dict)

    initialized: bool = False

    def record(self, a: Anomaly) -> None:
        self.anomalies.appendleft(a)

    def recent(self, limit: int = 50) -> list[dict]:
        return [asdict(a) for a in list(self.anomalies)[:limit]]


state = _AnomalyState()


async def _emit(a: Anomaly) -> None:
    """Log + optional webhook. Never raises."""
    state.record(a)
    log.warning(f"[anomaly] {a.severity.upper()} {a.kind}: {a.message}")
    url = settings.alert_webhook_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                url,
                json={"text": f":rotating_light: Atlas {a.severity}: {a.message}"},
            )
    except Exception as e:
        log.debug(f"[anomaly] webhook post failed: {e}")


def _ewma(prev: float | None, new: float) -> float:
    if prev is None:
        return new
    return _EWMA_ALPHA * new + (1 - _EWMA_ALPHA) * prev


async def _tick() -> None:
    snap = metrics.snapshot()
    req = int(snap.get("request_count", 0))
    err = int(snap.get("error_count", 0))
    cost = float(snap.get("total_cost_usd", 0.0))
    lat_total = float(req) * float(snap.get("avg_latency_ms", 0.0))

    d_req = req - state.prev_requests
    d_err = err - state.prev_errors
    d_cost = cost - state.prev_cost
    d_lat_total = lat_total - state.prev_latency_total
    interval_avg_latency = (d_lat_total / d_req) if d_req > 0 else 0.0

    # Update cumulative trackers up front so an early return still advances state.
    state.prev_requests = req
    state.prev_errors = err
    state.prev_cost = cost
    state.prev_latency_total = lat_total

    # First tick only establishes a baseline.
    if not state.initialized:
        state.initialized = True
        state.base_latency = interval_avg_latency or None
        state.base_cost = d_cost or None
        state.prev_health = _health_map()
        return

    # ── Error-rate spike ────────────────────────────────────────────────────
    if d_req >= settings.anomaly_min_requests:
        rate = d_err / d_req
        if rate >= settings.anomaly_error_rate_threshold:
            await _emit(
                Anomaly(
                    kind="error_rate_spike",
                    severity="critical" if rate >= 0.5 else "warning",
                    message=f"error rate {rate:.0%} ({d_err}/{d_req}) this interval",
                    value=round(rate, 3),
                    baseline=settings.anomaly_error_rate_threshold,
                    detected_at=now_ist().isoformat(),
                )
            )

    # ── Latency spike (vs EWMA baseline) ────────────────────────────────────
    if d_req >= settings.anomaly_min_requests and interval_avg_latency > 0:
        base = state.base_latency
        if base and interval_avg_latency > base * settings.anomaly_latency_factor:
            await _emit(
                Anomaly(
                    kind="latency_spike",
                    severity="warning",
                    message=(
                        f"avg latency {interval_avg_latency:.0f}ms is "
                        f"{interval_avg_latency / base:.1f}x baseline ({base:.0f}ms)"
                    ),
                    value=round(interval_avg_latency, 1),
                    baseline=round(base, 1),
                    detected_at=now_ist().isoformat(),
                )
            )
        state.base_latency = _ewma(state.base_latency, interval_avg_latency)

    # ── Cost surge (vs EWMA baseline) ───────────────────────────────────────
    if d_cost > 0:
        base = state.base_cost
        if base and base > 0 and d_cost > base * settings.anomaly_cost_factor:
            await _emit(
                Anomaly(
                    kind="cost_surge",
                    severity="warning",
                    message=(
                        f"interval cost ${d_cost:.4f} is "
                        f"{d_cost / base:.1f}x baseline (${base:.4f})"
                    ),
                    value=round(d_cost, 4),
                    baseline=round(base, 4),
                    detected_at=now_ist().isoformat(),
                )
            )
        state.base_cost = _ewma(state.base_cost, d_cost)

    # ── Provider health flap ────────────────────────────────────────────────
    cur_health = _health_map()
    for name, healthy in cur_health.items():
        was = state.prev_health.get(name, True)
        if was and not healthy:
            await _emit(
                Anomaly(
                    kind="provider_down",
                    severity="critical",
                    message=f"provider '{name}' went unhealthy",
                    value=0.0,
                    baseline=1.0,
                    detected_at=now_ist().isoformat(),
                )
            )
    state.prev_health = cur_health


def _health_map() -> dict:
    out = {}
    try:
        for p in provider_registry.list_providers():
            out[p["name"]] = bool(p.get("healthy"))
    except Exception:
        pass
    return out


async def run_anomaly_monitor() -> None:
    """Background loop. Started from app lifespan when enabled."""
    log.info(
        f"[anomaly-monitor] Started (interval={settings.anomaly_check_interval_seconds}s)"
    )
    # Prime baseline immediately so the first real tick has trackers set.
    try:
        await _tick()
    except Exception as e:
        log.debug(f"[anomaly-monitor] prime tick failed: {e}")

    while True:
        await asyncio.sleep(settings.anomaly_check_interval_seconds)
        try:
            await _tick()
        except Exception as exc:
            log.warning(f"[anomaly-monitor] tick failed: {exc}", exc_info=True)
