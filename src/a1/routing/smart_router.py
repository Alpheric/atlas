"""Atlas Smart Router — production-grade model selection engine.

Reads atlas_routing.yaml and implements:
  - Task-to-model routing with full fallback chains
  - Session stickiness (primary model per session)
  - Routing mode support: quality / balanced / low_cost / local_first
  - Context-length overrides (long → Gemini)
  - Shadow model selection for background learning
  - Structured routing decision log

This is the single source of truth for "which model handles this request".
The CorePipeline calls select() and gets back a RoutingDecision.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from a1.common.logging import get_logger
from a1.common.tokens import count_tokens
from a1.providers.registry import provider_registry

log = get_logger("routing.smart_router")

# ---------------------------------------------------------------------------
# Config loader — cached, reloaded on change (mtime check)
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path("config/atlas_routing.yaml")
_config_cache: dict = {}
_config_mtime: float = 0.0


def _load_config() -> dict:
    global _config_cache, _config_mtime
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
        if mtime != _config_mtime:
            _config_cache = yaml.safe_load(_CONFIG_PATH.read_text())
            _config_mtime = mtime
            log.debug("atlas_routing.yaml reloaded")
    except Exception as e:
        log.warning(f"Failed to load atlas_routing.yaml: {e}")
        if not _config_cache:
            _config_cache = _default_config()
    return _config_cache


def _default_config() -> dict:
    """Minimal fallback config when YAML is missing/corrupt."""
    return {
        "tasks": {
            "general": {"primary": "atlas-plan", "fallbacks": ["gemini-2.5-flash"], "shadow": "atlas-plan"},
            "coding": {"primary": "claude-sonnet-4-20250514", "fallbacks": ["gemini-2.5-pro", "qwen2.5-coder:7b"], "shadow": "atlas-code"},
            "debugging": {"primary": "claude-sonnet-4-20250514", "fallbacks": ["gemini-2.5-pro"], "shadow": "atlas-code"},
            "security": {"primary": "atlas-secure", "fallbacks": ["claude-sonnet-4-20250514"], "shadow": "atlas-secure"},
            "infra": {"primary": "atlas-infra", "fallbacks": ["claude-sonnet-4-20250514"], "shadow": "atlas-infra"},
            "data": {"primary": "atlas-data", "fallbacks": ["gemini-2.5-pro"], "shadow": "atlas-data"},
            "documents": {"primary": "atlas-books", "fallbacks": ["gemini-1.5-pro"], "shadow": "atlas-books"},
            "long_context": {"primary": "gemini-1.5-pro", "fallbacks": ["gemini-2.5-pro"], "shadow": "atlas-books"},
            "embeddings": {"primary": "nomic-embed-text:latest", "fallbacks": ["llama3.2:latest"], "shadow": None},
            "low_cost_background": {"primary": "gemini-2.0-flash-lite", "fallbacks": ["gemini-2.5-flash"], "shadow": None},
        },
        "context_routing": {"long_threshold": 180000, "long_model": "gemini-2.5-pro"},
        "session_stickiness": {"enabled": True, "compatible_groups": [], "always_reroute": ["embeddings", "long_context"]},
    }


# ---------------------------------------------------------------------------
# Atlas model → external (distillation teacher) map
# Mirrors what was in atlas_models.py but driven from YAML
# ---------------------------------------------------------------------------

# Maps atlas-* / public model names to the actual provider-level model names
_ATLAS_TO_PROVIDER_MODEL: dict[str, str] = {
    "atlas-plan": "claude-sonnet-4-20250514",
    "atlas-code": "claude-sonnet-4-20250514",
    "atlas-secure": "claude-sonnet-4-20250514",
    "atlas-infra": "claude-sonnet-4-20250514",
    "atlas-data": "claude-sonnet-4-20250514",
    "atlas-books": "claude-sonnet-4-20250514",
    "atlas-audit": "claude-sonnet-4-20250514",
    "Atlas": "claude-sonnet-4-20250514",
}

# Public aliases that should be treated as "Atlas" (auto-select)
_ATLAS_PUBLIC_NAMES = frozenset({
    "Atlas", "atlas", "atlas-plan", "atlas-code", "atlas-secure",
    "atlas-infra", "atlas-data", "atlas-books", "atlas-audit",
    "alpheric-1", "auto", "auto:fast", "auto:cheap", "local",
})


# ---------------------------------------------------------------------------
# Routing decision dataclass
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    """Everything the pipeline needs to execute and log a routing choice."""

    # Primary selection
    primary_model: str = ""           # model to run (may be atlas-* or provider model)
    primary_provider: str = ""        # provider name
    provider_model: str = ""          # actual model name sent to the provider
    task_type: str = "general"
    atlas_model: str | None = None    # atlas-* model if applicable

    # Fallback chain for the executor
    fallback_models: list[str] = field(default_factory=list)

    # Shadow / distillation
    shadow_model: str | None = None   # atlas-* model to run in background

    # Context metadata
    context_tokens: int = 0
    routing_mode: str = "balanced"    # quality / balanced / low_cost / local_first
    confidence: float = 0.0

    # Session stickiness
    is_sticky: bool = False           # True if re-using session's primary model
    sticky_override: bool = False     # True if task change forced a switch

    # Observability
    selection_reason: str = ""
    latency_budget_ms: int | None = None


# ---------------------------------------------------------------------------
# Core router
# ---------------------------------------------------------------------------

class SmartRouter:
    """Stateless routing engine — call select() per request."""

    def select(
        self,
        task_type: str,
        routing_mode: str = "balanced",
        context_tokens: int = 0,
        session_primary_model: str | None = None,
        session_primary_task: str | None = None,
        force_model: str | None = None,
    ) -> RoutingDecision:
        """Select the best model for this request.

        Args:
            task_type: Classified task type from classifier.py
            routing_mode: quality | balanced | low_cost | local_first
            context_tokens: Total tokens in the current context window
            session_primary_model: The model chosen in earlier turns of this session
            session_primary_task: The task_type from earlier turns
            force_model: Bypass routing and use this model directly

        Returns:
            RoutingDecision with all fields populated.
        """
        cfg = _load_config()
        decision = RoutingDecision(task_type=task_type, routing_mode=routing_mode)
        decision.context_tokens = context_tokens

        # ── Force override ─────────────────────────────────────────────────
        if force_model:
            decision.primary_model = force_model
            decision.selection_reason = f"forced:{force_model}"
            self._resolve_provider(decision)
            return decision

        # ── Context-length override ────────────────────────────────────────
        ctx_cfg = cfg.get("context_routing", {})
        very_long = ctx_cfg.get("very_long_threshold", 900_000)
        long = ctx_cfg.get("long_threshold", 180_000)
        if context_tokens >= very_long:
            decision.primary_model = ctx_cfg.get("very_long_model", "gemini-1.5-pro")
            decision.fallback_models = [ctx_cfg.get("long_model", "gemini-2.5-pro")]
            decision.selection_reason = f"context_very_long:{context_tokens}"
            decision.task_type = "long_context"
            self._resolve_provider(decision)
            return decision
        if context_tokens >= long:
            decision.primary_model = ctx_cfg.get("long_model", "gemini-2.5-pro")
            decision.fallback_models = [ctx_cfg.get("long_fallback", "gemini-1.5-pro")]
            decision.selection_reason = f"context_long:{context_tokens}"
            decision.task_type = "long_context"
            self._resolve_provider(decision)
            return decision

        # ── Task config ────────────────────────────────────────────────────
        tasks_cfg = cfg.get("tasks", {})
        task_cfg = tasks_cfg.get(task_type, tasks_cfg.get("general", {}))
        base_primary = task_cfg.get("primary", "atlas-plan")
        base_fallbacks = list(task_cfg.get("fallbacks", []))
        shadow = task_cfg.get("shadow")

        # ── Routing mode overrides ─────────────────────────────────────────
        mode_cfg = cfg.get("mode_overrides", {}).get(routing_mode, {})
        primary = mode_cfg.get(task_type, base_primary)

        # ── Session stickiness ─────────────────────────────────────────────
        sticky_cfg = cfg.get("session_stickiness", {})
        always_reroute = set(sticky_cfg.get("always_reroute", []))
        sticky_enabled = sticky_cfg.get("enabled", True)

        if (
            sticky_enabled
            and session_primary_model
            and task_type not in always_reroute
            and self._is_compatible_task(task_type, session_primary_task, cfg)
        ):
            # Check that session model is still available
            sp = self._provider_for_model(session_primary_model)
            if sp and provider_registry.is_healthy(sp):
                decision.primary_model = session_primary_model
                decision.is_sticky = True
                decision.selection_reason = f"session_sticky:{session_primary_model}"
                # Keep standard fallbacks as safety net
                decision.fallback_models = [primary] + base_fallbacks
                decision.shadow_model = shadow
                self._resolve_provider(decision)
                return decision

        # ── Normal selection ───────────────────────────────────────────────
        decision.primary_model = primary
        decision.fallback_models = base_fallbacks
        decision.shadow_model = shadow
        decision.selection_reason = f"task:{task_type}/mode:{routing_mode}"
        self._resolve_provider(decision)
        return decision

    def _resolve_provider(self, decision: RoutingDecision) -> None:
        """Fill in provider_name, provider_model, atlas_model from primary_model."""
        model = decision.primary_model

        # Check if it's an atlas-* model
        if model in _ATLAS_TO_PROVIDER_MODEL or model.startswith("atlas-"):
            decision.atlas_model = model
            provider_model = _ATLAS_TO_PROVIDER_MODEL.get(model, "claude-sonnet-4-20250514")
            # For the distillation path, provider_model is the teacher model
            decision.provider_model = provider_model
            decision.primary_provider = "claude-cli"
        else:
            # Direct model — resolve provider via registry
            decision.atlas_model = None
            decision.provider_model = model
            p = provider_registry.get_provider_for_model(model)
            decision.primary_provider = p.name if p else "unknown"

    def _provider_for_model(self, model: str) -> str | None:
        """Return provider name for a model, None if unresolvable."""
        if model in _ATLAS_TO_PROVIDER_MODEL or model.startswith("atlas-"):
            return "claude-cli"
        p = provider_registry.get_provider_for_model(model)
        return p.name if p else None

    def _is_compatible_task(
        self,
        new_task: str,
        session_task: str | None,
        cfg: dict,
    ) -> bool:
        """True if new_task is in the same compatibility group as session_task."""
        if not session_task or new_task == session_task:
            return True
        groups = cfg.get("session_stickiness", {}).get("compatible_groups", [])
        for group in groups:
            if new_task in group and session_task in group:
                return True
        return False

    def get_shadow_model(self, task_type: str) -> str | None:
        """Return the atlas-* model that should shadow this task type."""
        cfg = _load_config()
        task_cfg = cfg.get("tasks", {}).get(task_type, {})
        shadow = task_cfg.get("shadow")
        if shadow == "null" or shadow is None:
            return None
        return shadow

    def get_fallbacks(self, task_type: str) -> list[str]:
        """Return ordered fallback models for a task type."""
        cfg = _load_config()
        return list(cfg.get("tasks", {}).get(task_type, {}).get("fallbacks", []))

    def is_atlas_model(self, model: str) -> bool:
        return model in _ATLAS_TO_PROVIDER_MODEL or model.startswith("atlas-")

    def is_public_atlas_name(self, model: str) -> bool:
        return model in _ATLAS_PUBLIC_NAMES


# Singleton
smart_router = SmartRouter()
