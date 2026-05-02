"""Layer 4 — Conversation Health Monitor.

Background asyncio task that scans recent conversations and writes health scores
to the ``conversation_health`` table.

Health score formula (0.0–1.0):
  0.40 × avg_quality_signal   (from quality_signals table, type=auto_eval)
  0.30 × (1 - stuck_penalty)  (1.0 = never stuck, 0.0 = 3+ repeated user messages)
  0.20 × (1 - abandon_penalty)(1.0 = properly resolved, 0.0 = last msg is user)
  0.10 × (1 - length_penalty) (long unresolved convs get a small penalty)

Flags written to the flags JSON column:
  stuck:       True if the same user message appears 3+ times (80%+ similarity)
  abandoned:   True if the last message is from the user (no assistant follow-up)
  low_quality: True if avg auto_eval quality signal < 0.35
  self_healed: True if any message has a self_healed routing_decision
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from a1.common.logging import get_logger

log = get_logger("healing.conversation_monitor")


def _jaccard_similarity(a: str, b: str) -> float:
    """Simple word-level Jaccard similarity for stuck-detection."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union else 0.0


def _is_stuck(user_messages: list[str], threshold: float = 0.80) -> bool:
    """Return True if any user message appears 3+ times (≥ threshold similarity)."""
    if len(user_messages) < 3:
        return False
    for i, a in enumerate(user_messages):
        count = 1
        for j, b in enumerate(user_messages):
            if i == j:
                continue
            if _jaccard_similarity(a, b) >= threshold:
                count += 1
                if count >= 3:
                    return True
    return False


async def _score_conversation(conv, db) -> dict:
    """Compute health score and flags for a single Conversation object."""
    from sqlalchemy import func, select

    from a1.db.models import Message, QualitySignal, RoutingDecision

    msgs = sorted(conv.messages or [], key=lambda m: m.sequence)
    user_msgs = [m.content for m in msgs if m.role == "user"]
    asst_msgs = [m for m in msgs if m.role == "assistant"]
    turn_count = len(msgs)

    # ── Flag: abandoned ───────────────────────────────────────────────────────
    abandoned = bool(msgs) and msgs[-1].role == "user"

    # ── Flag: stuck ───────────────────────────────────────────────────────────
    stuck = _is_stuck(user_msgs)

    # ── Flag: self_healed ─────────────────────────────────────────────────────
    has_healed = any(
        m.routing_decision and m.routing_decision.self_healed
        for m in asst_msgs
        if m.routing_decision
    )

    # ── Avg quality from quality_signals ─────────────────────────────────────
    asst_ids = [m.id for m in asst_msgs]
    avg_quality: float = 0.5  # neutral default when no signals yet
    if asst_ids:
        row = await db.execute(
            select(func.avg(QualitySignal.value))
            .where(
                QualitySignal.message_id.in_(asst_ids),
                QualitySignal.signal_type == "auto_eval",
            )
        )
        val = row.scalar()
        if val is not None:
            avg_quality = float(val)

    # ── Flag: low_quality ─────────────────────────────────────────────────────
    low_quality = avg_quality < 0.35

    # ── Health score ─────────────────────────────────────────────────────────
    stuck_penalty = 1.0 if stuck else 0.0
    abandon_penalty = 1.0 if abandoned else 0.0
    length_penalty = min(max(turn_count - 20, 0) / 40, 1.0)  # penalty ramps from 20–60 turns

    health_score = (
        0.40 * avg_quality
        + 0.30 * (1.0 - stuck_penalty)
        + 0.20 * (1.0 - abandon_penalty)
        + 0.10 * (1.0 - length_penalty)
    )
    health_score = round(min(max(health_score, 0.0), 1.0), 3)

    flags = {
        "stuck": stuck,
        "abandoned": abandoned,
        "low_quality": low_quality,
        "self_healed": has_healed,
    }

    return {
        "health_score": health_score,
        "flags": flags,
        "avg_quality": avg_quality,
        "turn_count": turn_count,
    }


async def _scan_recent_conversations() -> None:
    """Scan conversations from the last 24h and upsert health rows."""
    import uuid as _uuid
    from datetime import timezone as _tz

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from a1.common.tz import now_ist
    from a1.db.engine import async_session
    from a1.db.models import Conversation, ConversationHealth, Message

    cutoff = now_ist() - timedelta(hours=24)

    async with async_session() as db:
        # Load recent conversations with messages + routing decisions + quality signals
        result = await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.messages)
                .selectinload(Message.routing_decision),
                selectinload(Conversation.messages)
                .selectinload(Message.quality_signals),
            )
            .where(Conversation.updated_at >= cutoff)
        )
        conversations = list(result.scalars().all())

        scanned = 0
        for conv in conversations:
            try:
                scores = await _score_conversation(conv, db)
                now = now_ist()

                # Upsert conversation_health
                existing = await db.execute(
                    select(ConversationHealth).where(
                        ConversationHealth.conversation_id == conv.id
                    )
                )
                health_row = existing.scalar_one_or_none()

                async with db.begin_nested():
                    if health_row:
                        health_row.health_score = scores["health_score"]
                        health_row.flags = scores["flags"]
                        health_row.avg_quality = scores["avg_quality"]
                        health_row.turn_count = scores["turn_count"]
                        health_row.checked_at = now
                    else:
                        health_row = ConversationHealth(
                            conversation_id=conv.id,
                            health_score=scores["health_score"],
                            flags=scores["flags"],
                            avg_quality=scores["avg_quality"],
                            turn_count=scores["turn_count"],
                            checked_at=now,
                        )
                        db.add(health_row)

                scanned += 1
            except Exception as exc:
                log.debug(f"[health-monitor] Skipped conv {conv.id}: {exc}")

        try:
            await db.commit()
        except Exception as exc:
            log.warning(f"[health-monitor] Commit failed: {exc}")
            await db.rollback()

    log.info(f"[health-monitor] Scanned {scanned}/{len(conversations)} conversations")


async def run_health_monitor() -> None:
    """Continuous background loop — scans conversations every interval seconds."""
    from config.settings import settings

    log.info(
        f"[health-monitor] Started (interval={settings.health_monitor_interval_seconds}s)"
    )
    while True:
        try:
            await _scan_recent_conversations()
        except Exception as exc:
            log.warning(f"[health-monitor] Scan cycle failed: {exc}", exc_info=True)
        await asyncio.sleep(settings.health_monitor_interval_seconds)
