"""Routing strategy: combines classifier + scorer + exploration."""

import random

from sqlalchemy import func, select

from a1.common.logging import get_logger
from a1.providers.registry import provider_registry
from a1.routing.scorer import ModelCandidate, get_cold_start_model, score_candidates
from config.settings import settings

log = get_logger("routing.strategy")

# Minimum routing_decisions samples required before live scorer activates
_LIVE_SCORER_MIN_SAMPLES = 20


def _get_provider_for(model: str) -> str:
    """Resolve provider name for a model via registry (single source of truth)."""
    provider = provider_registry.get_provider_for_model(model)
    if provider:
        return provider.name
    # Last resort: local
    return "ollama"


async def _build_candidates_from_db(task_type: str) -> list[ModelCandidate]:
    """Query routing_decisions for per-model avg latency/cost when 20+ samples exist.

    Returns a ModelCandidate list for passing to score_candidates().
    """
    try:
        from a1.db.engine import async_session
        from a1.db.models import RoutingDecision

        async with async_session() as session:
            stmt = (
                select(
                    RoutingDecision.model,
                    RoutingDecision.provider,
                    func.avg(RoutingDecision.latency_ms).label("avg_latency_ms"),
                    func.avg(RoutingDecision.cost_usd).label("avg_cost_usd"),
                    func.count(RoutingDecision.id).label("sample_count"),
                )
                .where(RoutingDecision.task_type == task_type)
                .group_by(RoutingDecision.model, RoutingDecision.provider)
                .having(func.count(RoutingDecision.id) >= _LIVE_SCORER_MIN_SAMPLES)
            )
            result = await session.execute(stmt)
            rows = result.all()

        candidates = [
            ModelCandidate(
                provider=row.provider,
                model=row.model,
                avg_quality=0.0,  # quality scoring deferred until eval signals exist
                avg_latency_ms=float(row.avg_latency_ms),
                avg_cost_usd=float(row.avg_cost_usd),
                sample_count=row.sample_count,
            )
            for row in rows
        ]
        return candidates
    except Exception as e:
        log.warning(f"Failed to build candidates from DB for {task_type}: {e}")
        return []


async def select_model(task_type: str, strategy: str) -> tuple[str, str]:
    """Select the best model for a task type and strategy.
    Returns (model_name, provider_name).

    For lowest_cost and lowest_latency strategies, uses live performance data from
    routing_decisions when 20+ samples exist per model. Falls back to cold-start
    defaults from routing_policy.yaml when insufficient data.
    """
    model, fallbacks = get_cold_start_model(task_type)

    # Live scorer: activated for cost/latency strategies when enough data exists
    if strategy in ("lowest_cost", "lowest_latency"):
        candidates = await _build_candidates_from_db(task_type)
        if candidates:
            ranked = score_candidates(candidates, strategy)
            for candidate in ranked:
                provider_name = _get_provider_for(candidate.model)
                if provider_registry.is_healthy(provider_name):
                    log.info(
                        f"Live scorer: {strategy} selected {candidate.model} "
                        f"(latency={candidate.avg_latency_ms:.0f}ms "
                        f"cost=${candidate.avg_cost_usd:.4f} "
                        f"n={candidate.sample_count}) for {task_type}"
                    )
                    return candidate.model, provider_name

    # Epsilon-greedy exploration: with probability epsilon, pick a random alternative
    if random.random() < settings.exploration_rate and fallbacks:
        model = random.choice(fallbacks)
        log.info(f"Exploration: selected {model} instead of default for {task_type}")

    provider_name = _get_provider_for(model)

    # Check provider health; fall back if unhealthy
    if not provider_registry.is_healthy(provider_name):
        down_secs = provider_registry.get_unhealthy_duration(provider_name)
        if down_secs is not None and down_secs >= 300:
            log.critical(
                f"Provider {provider_name} has been unhealthy for "
                f"{down_secs:.0f}s — escalation required"
            )
        log.warning(f"Provider {provider_name} unhealthy, trying fallbacks")
        seen_providers: set[str] = {provider_name}
        for fb_model in fallbacks:
            fb_provider = _get_provider_for(fb_model)
            if fb_provider in seen_providers:
                continue  # skip: already determined this provider is unhealthy in this chain
            seen_providers.add(fb_provider)
            if provider_registry.is_healthy(fb_provider):
                return fb_model, fb_provider

        # Last resort: prefer fast English models
        preferred_fallbacks = ["gemma3:12b", "llama3.2:latest", "mistral:7b", "deepseek-coder:6.7b"]
        for pref in preferred_fallbacks:
            for name, provider in provider_registry.healthy_providers.items():
                if provider.supports_model(pref):
                    return pref, name

        # Any healthy model
        for name, provider in provider_registry.healthy_providers.items():
            models = provider.list_models()
            if models:
                return models[0].name, name

        # Absolute fallback: try ollama
        return "local", "ollama"

    return model, provider_name
