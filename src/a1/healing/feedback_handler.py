"""Layer 3 — Feedback-Triggered Improvement.

When a user submits thumbs-down on an assistant message, this handler:
  1. Loads the conversation and the flagged message
  2. Finds the user turn that prompted the bad response
  3. Asks Claude to regenerate a significantly better answer
  4. Stores the improved response as a new Message (role=assistant)
  5. Creates a positive QualitySignal on the improved message
  6. Marks the original message's routing_decision as self_healed (if present)

Designed to run as asyncio.create_task() — all errors are logged, never raised.
"""

from __future__ import annotations

from a1.common.logging import get_logger

log = get_logger("healing.feedback_handler")

_IMPROVE_PROMPT = """\
The user was unsatisfied with the following assistant response. \
Generate a significantly better response to the original user message.

Requirements:
- More complete and accurate than the original
- Directly answers what the user asked
- Appropriate for task type: {task_type}
- Respond with ONLY the improved response (no preamble, no meta-commentary)

=== ORIGINAL USER MESSAGE ===
{user_message}

=== UNSATISFACTORY RESPONSE ===
{bad_response}
"""


async def handle_thumbs_down(
    conversation_id: str,
    message_id: str,
    *,
    task_type: str | None = None,
) -> None:
    """Regenerate an improved response for a thumbs-down assistant message.

    Args:
        conversation_id: UUID string of the conversation.
        message_id:      UUID string of the thumbs-downed assistant message.
        task_type:       Optional task-type hint (used in the improvement prompt).
    """
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from a1.db.engine import async_session
    from a1.db.models import Message, QualitySignal, RoutingDecision
    from a1.db.repositories import MessageRepo
    from a1.providers.registry import provider_registry
    from a1.proxy.request_models import ChatCompletionRequest
    from config.settings import settings

    try:
        async with async_session() as db:
            # ── 1. Load the flagged assistant message ──────────────────────────
            msg_result = await db.execute(
                select(Message)
                .options(selectinload(Message.routing_decision))
                .where(Message.id == _uuid.UUID(message_id))
            )
            flagged_msg: Message | None = msg_result.scalar_one_or_none()

            if flagged_msg is None or flagged_msg.role != "assistant":
                log.warning(f"[feedback-handler] Message {message_id} not found or not assistant")
                return

            # ── 2. Find the preceding user turn ───────────────────────────────
            conv_msgs_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == _uuid.UUID(conversation_id))
                .order_by(Message.sequence.asc())
            )
            all_msgs: list[Message] = list(conv_msgs_result.scalars().all())

            user_msg_text: str | None = None
            for i, m in enumerate(all_msgs):
                if str(m.id) == message_id and i > 0:
                    if all_msgs[i - 1].role == "user":
                        user_msg_text = all_msgs[i - 1].content
                    break

            if not user_msg_text:
                log.warning(f"[feedback-handler] No preceding user turn for message {message_id}")
                return

            # ── 3. Determine task type ─────────────────────────────────────────
            effective_task_type = task_type or "general"
            if flagged_msg.routing_decision and flagged_msg.routing_decision.task_type:
                effective_task_type = flagged_msg.routing_decision.task_type

            # ── 4. Generate improved response ─────────────────────────────────
            provider = provider_registry.get_provider("claude-cli")
            if provider is None or not provider_registry.is_healthy("claude-cli"):
                log.warning("[feedback-handler] claude-cli not available, skipping")
                return

            prompt = _IMPROVE_PROMPT.format(
                user_message=user_msg_text[:2000],
                bad_response=flagged_msg.content[:3000],
                task_type=effective_task_type,
            )

            req = ChatCompletionRequest(
                model=settings.distillation_claude_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.4,
            )
            resp = await provider.complete(req)
            improved_text = resp.choices[0].message.content if resp.choices else None

            if not improved_text or len(improved_text.strip()) < 20:
                log.warning("[feedback-handler] Improvement too short or empty, skipping")
                return

            improved_text = improved_text.strip()

            # ── 5. Store improved message + quality signals ────────────────────
            async with db.begin():
                # New sequence = flagged sequence + 1 (or max + 1 to avoid constraint violation)
                existing_seqs = {m.sequence for m in all_msgs}
                new_seq = flagged_msg.sequence + 1
                while new_seq in existing_seqs:
                    new_seq += 1

                new_msg = Message(
                    conversation_id=_uuid.UUID(conversation_id),
                    role="assistant",
                    content=improved_text,
                    sequence=new_seq,
                )
                db.add(new_msg)
                await db.flush()  # get new_msg.id

                # Positive quality signal on the improved message
                pos_signal = QualitySignal(
                    message_id=new_msg.id,
                    signal_type="auto_eval",
                    value=0.9,
                    evaluator="feedback_regen",
                )
                db.add(pos_signal)

                # Negative quality signal on the original
                neg_signal = QualitySignal(
                    message_id=flagged_msg.id,
                    signal_type="thumbs",
                    value=0.1,
                    evaluator="user_thumbsdown_regen",
                )
                db.add(neg_signal)

                # Mark original routing_decision as self_healed
                if flagged_msg.routing_decision:
                    flagged_msg.routing_decision.self_healed = True
                    if not flagged_msg.routing_decision.heal_score_before:
                        flagged_msg.routing_decision.heal_score_before = 0.1

            log.info(
                f"[feedback-handler] Regenerated message {message_id} → new msg {new_msg.id}"
                f" for conv {conversation_id} (task={effective_task_type})"
            )

    except Exception as exc:
        log.error(f"[feedback-handler] Failed: {exc}", exc_info=True)
