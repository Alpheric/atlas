"""Provider failover circuit breaker (Phase 3.3).

Tracks consecutive failures per provider. After `threshold` consecutive
failures the breaker OPENS and the provider is reported unavailable for
`cooldown` seconds. After the cooldown it goes HALF_OPEN: a single probe is
allowed through — success CLOSES the breaker, another failure re-OPENS it.

This prevents the pipeline from repeatedly trying a provider that's down (each
attempt costing a timeout) and makes failover to healthy providers fast.

State is in-process and best-effort; it complements (does not replace) the
registry's periodic health checks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from a1.common.logging import get_logger
from config.settings import settings

log = get_logger("providers.circuit_breaker")

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass
class _BreakerState:
    failures: int = 0
    state: str = CLOSED
    opened_at: float = 0.0
    last_failure: float = 0.0


@dataclass
class CircuitBreaker:
    breakers: dict[str, _BreakerState] = field(default_factory=dict)

    def _get(self, name: str) -> _BreakerState:
        b = self.breakers.get(name)
        if b is None:
            b = _BreakerState()
            self.breakers[name] = b
        return b

    def record_success(self, name: str) -> None:
        b = self._get(name)
        if b.state != CLOSED or b.failures:
            log.info(f"[circuit] {name} → closed (recovered)")
        b.failures = 0
        b.state = CLOSED
        b.opened_at = 0.0

    def record_failure(self, name: str) -> None:
        if not settings.circuit_breaker_enabled:
            return
        b = self._get(name)
        b.failures += 1
        b.last_failure = time.time()
        if b.state == HALF_OPEN:
            # Probe failed → re-open
            b.state = OPEN
            b.opened_at = time.time()
            log.warning(f"[circuit] {name} → open again (half-open probe failed)")
        elif b.failures >= settings.circuit_breaker_threshold and b.state == CLOSED:
            b.state = OPEN
            b.opened_at = time.time()
            log.warning(
                f"[circuit] {name} → OPEN after {b.failures} consecutive failures "
                f"(cooldown {settings.circuit_breaker_cooldown_seconds}s)"
            )

    def is_available(self, name: str) -> bool:
        """True if the provider may be tried. Transitions OPEN→HALF_OPEN after
        the cooldown so exactly one probe is admitted."""
        if not settings.circuit_breaker_enabled:
            return True
        b = self.breakers.get(name)
        if b is None or b.state == CLOSED:
            return True
        if b.state == HALF_OPEN:
            return True  # probe already admitted; awaiting its result
        # OPEN — check cooldown
        if time.time() - b.opened_at >= settings.circuit_breaker_cooldown_seconds:
            b.state = HALF_OPEN
            log.info(f"[circuit] {name} → half-open (admitting one probe)")
            return True
        return False

    def status(self) -> list[dict]:
        now = time.time()
        out = []
        for name, b in self.breakers.items():
            out.append(
                {
                    "provider": name,
                    "state": b.state,
                    "consecutive_failures": b.failures,
                    "seconds_since_opened": round(now - b.opened_at, 1) if b.opened_at else None,
                    "available": self.is_available(name),
                }
            )
        return out


# Singleton
circuit_breaker = CircuitBreaker()
