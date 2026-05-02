"""Conversations, sessions, feedback, and PII stats endpoints.

Endpoints:
  GET  /conversations
  GET  /conversations/stats
  GET  /conversations/{conv_id}
  POST /conversations/{conv_id}/feedback
  GET  /sessions
  GET  /sessions/{session_id}
  GET  /pii/stats
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from a1.db.repositories import ConversationRepo, QualityRepo
from a1.dependencies import get_db
from config.settings import settings

router = APIRouter()


def _conv_latest_routing(conv) -> tuple[str | None, str | None]:
    """Return (model, task_type) from the latest assistant message with a routing decision."""
    for msg in sorted(conv.messages or [], key=lambda m: m.sequence, reverse=True):
        if msg.role == "assistant" and msg.routing_decision:
            return msg.routing_decision.model, msg.routing_decision.task_type
    return None, None


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    source: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from a1.db.models import ConversationHealth

    repo = ConversationRepo(db)
    conversations = await repo.list_recent(
        limit=limit,
        offset=offset,
        search=search,
        date_from=date_from,
        date_to=date_to,
        source=source,
    )
    total = await repo.count(search=search, date_from=date_from, date_to=date_to, source=source)

    # Batch-load health scores for all returned conversations
    conv_ids = [c.id for c in conversations]
    health_map: dict = {}
    if conv_ids:
        health_rows = await db.execute(
            select(ConversationHealth).where(ConversationHealth.conversation_id.in_(conv_ids))
        )
        for h in health_rows.scalars().all():
            health_map[h.conversation_id] = h

    return {
        "data": [
            {
                "id": str(c.id),
                "source": c.source,
                "user_id": c.user_id,
                "message_count": sum(
                    1 for m in (c.messages or []) if m.role in ("user", "assistant")
                ),
                "model": _conv_latest_routing(c)[0],
                "task_type": _conv_latest_routing(c)[1],
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "metadata": c.metadata_,
                "health_score": health_map[c.id].health_score if c.id in health_map else None,
                "health_flags": health_map[c.id].flags if c.id in health_map else None,
            }
            for c in conversations
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/conversations/stats")
async def conversation_stats(db: AsyncSession = Depends(get_db)):
    """Conversation summary statistics for dashboard KPIs."""
    from sqlalchemy import func as sqlfunc
    from sqlalchemy import select as sqlselect

    from a1.db.models import Conversation, Message, RoutingDecision

    total = await db.execute(sqlselect(sqlfunc.count(Conversation.id)))
    total_convs = total.scalar() or 0

    total_msgs = await db.execute(
        sqlselect(sqlfunc.count(Message.id)).where(Message.role.in_(["user", "assistant"]))
    )
    total_messages = total_msgs.scalar() or 0

    # Source breakdown
    source_q = await db.execute(
        sqlselect(Conversation.source, sqlfunc.count(Conversation.id)).group_by(Conversation.source)
    )
    sources = {row[0]: row[1] for row in source_q.all()}

    # User breakdown
    user_q = await db.execute(
        sqlselect(sqlfunc.count(sqlfunc.distinct(Conversation.user_id))).where(
            Conversation.user_id.isnot(None)
        )
    )
    unique_users = user_q.scalar() or 0

    # Avg messages per conversation
    avg_msgs = total_messages / max(total_convs, 1)

    # Routing decisions count
    rd_count = await db.execute(sqlselect(sqlfunc.count(RoutingDecision.id)))
    total_decisions = rd_count.scalar() or 0

    # Recent activity (last 24h)
    from a1.common.tz import now_ist

    cutoff = now_ist() - timedelta(hours=24)
    recent_q = await db.execute(
        sqlselect(sqlfunc.count(Conversation.id)).where(Conversation.created_at >= cutoff)
    )
    recent_24h = recent_q.scalar() or 0

    return {
        "total_conversations": total_convs,
        "total_messages": total_messages,
        "identified_users": unique_users,
        "avg_messages_per_conversation": round(avg_msgs, 1),
        "total_routing_decisions": total_decisions,
        "recent_24h": recent_24h,
        "sources": sources,
    }


@router.get("/conversations/health")
async def list_conversation_health(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return conversation health scores for the dashboard list view.

    Returns the most recent ``limit`` health records sorted by health_score ascending
    (worst conversations first — most actionable).
    """
    from sqlalchemy import select

    from a1.db.models import ConversationHealth

    result = await db.execute(
        select(ConversationHealth)
        .order_by(ConversationHealth.health_score.asc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return {
        "data": [
            {
                "conversation_id": str(r.conversation_id),
                "health_score": r.health_score,
                "flags": r.flags,
                "avg_quality": r.avg_quality,
                "turn_count": r.turn_count,
                "checked_at": r.checked_at.isoformat() if r.checked_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from a1.db.models import ConversationHealth

    repo = ConversationRepo(db)
    conv = await repo.get(uuid.UUID(conv_id))
    if not conv:
        raise HTTPException(404, "Conversation not found")

    # Aggregate cost/token totals + self-heal stats
    total_prompt_tokens = sum(
        m.routing_decision.prompt_tokens or 0 for m in conv.messages if m.routing_decision
    )
    total_completion_tokens = sum(
        m.routing_decision.completion_tokens or 0 for m in conv.messages if m.routing_decision
    )
    total_cost_usd = sum(
        float(m.routing_decision.cost_usd) for m in conv.messages if m.routing_decision
    )
    healed_count = sum(
        1 for m in conv.messages if m.routing_decision and m.routing_decision.self_healed
    )

    # Load conversation health record
    health_row = await db.execute(
        select(ConversationHealth).where(
            ConversationHealth.conversation_id == conv.id
        )
    )
    health = health_row.scalar_one_or_none()

    def _quality_score(msg) -> float | None:
        """Return the latest auto_eval quality score for a message, or None."""
        signals = [s for s in (msg.quality_signals or []) if s.signal_type == "auto_eval"]
        if signals:
            return round(sorted(signals, key=lambda s: s.created_at)[-1].value, 3)
        return None

    return {
        "id": str(conv.id),
        "source": conv.source,
        "user_id": conv.user_id,
        "metadata": conv.metadata_,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cost_usd": round(total_cost_usd, 6),
        "healed_count": healed_count,
        "health": {
            "score": health.health_score,
            "flags": health.flags,
            "avg_quality": health.avg_quality,
            "turn_count": health.turn_count,
            "checked_at": health.checked_at.isoformat() if health.checked_at else None,
        } if health else None,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "token_count": m.token_count,
                "sequence": m.sequence,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "quality_score": _quality_score(m),
                "routing_decision": {
                    "provider": m.routing_decision.provider,
                    "model": m.routing_decision.model,
                    "task_type": m.routing_decision.task_type,
                    "strategy": m.routing_decision.strategy,
                    "latency_ms": m.routing_decision.latency_ms,
                    "cost_usd": float(m.routing_decision.cost_usd),
                    "prompt_tokens": m.routing_decision.prompt_tokens,
                    "completion_tokens": m.routing_decision.completion_tokens,
                    "self_healed": m.routing_decision.self_healed,
                    "heal_score_before": m.routing_decision.heal_score_before,
                    "is_local": m.routing_decision.is_local,
                    "cache_hit": m.routing_decision.cache_hit,
                    "error": m.routing_decision.error,
                }
                if m.routing_decision
                else None,
                "quality_signals": [
                    {
                        "type": s.signal_type,
                        "value": s.value,
                        "evaluator": s.evaluator,
                        "created_at": s.created_at.isoformat() if s.created_at else None,
                    }
                    for s in sorted(m.quality_signals or [], key=lambda s: s.created_at)
                ],
            }
            for m in sorted(conv.messages, key=lambda x: x.sequence)
        ],
    }


@router.post("/conversations/{conv_id}/feedback")
async def add_feedback(
    conv_id: str,
    message_id: str,
    signal_type: str = "thumbs",
    value: float = 1.0,
    db: AsyncSession = Depends(get_db),
):
    import asyncio

    repo = QualityRepo(db)
    signal = await repo.add_signal(
        message_id=uuid.UUID(message_id),
        signal_type=signal_type,
        value=value,
        evaluator="user:dashboard",
    )

    # Thumbs-down (value <= -1 or value < 0.5 for thumbs type) → regenerate
    if signal_type == "thumbs" and value < 0.5 and settings.feedback_regen_enabled:
        from a1.healing.feedback_handler import handle_thumbs_down

        asyncio.create_task(
            handle_thumbs_down(conv_id, message_id)
        )

    return {"id": str(signal.id), "status": "recorded"}


# --- Manual Regenerate ---
@router.post("/conversations/{conv_id}/messages/{msg_id}/regenerate")
async def regenerate_message(
    conv_id: str,
    msg_id: str,
):
    """Manually trigger regeneration for a specific assistant message.

    This calls the same feedback handler as thumbs-down, without recording
    a quality signal. Useful for dashboard "Regenerate" buttons.
    Returns immediately; the improved message appears asynchronously.
    """
    import asyncio

    if not settings.feedback_regen_enabled:
        from fastapi import HTTPException
        raise HTTPException(503, "Feedback regeneration is disabled (A1_FEEDBACK_REGEN_ENABLED=false)")

    from a1.healing.feedback_handler import handle_thumbs_down

    asyncio.create_task(handle_thumbs_down(conv_id, msg_id))
    return {"status": "regeneration_started", "message_id": msg_id}


# --- Sessions ---
@router.get("/sessions")
async def list_sessions():
    """List all active sessions."""
    from a1.session.manager import session_manager

    return {"data": session_manager.list_active()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session detail with message history."""
    from a1.session.manager import session_manager

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")
    return {
        "id": session.id,
        "user_id": session.user_id,
        "message_count": len(session.messages),
        "messages": [
            {"role": m.role, "content": m.content[:200], "timestamp": m.timestamp}
            for m in session.messages
        ],
        "created_at": session.created_at,
        "last_activity": session.last_activity,
    }


# --- PII Stats ---
@router.get("/pii/stats")
async def pii_stats():
    """PII masking statistics."""
    from a1.security.pii_masker import get_mask_stats

    return {
        "enabled": settings.pii_masking_enabled,
        "external_only": settings.pii_mask_for_external_only,
        "patterns": settings.pii_patterns,
        **get_mask_stats(),
    }
