"""Layer 1 — Response Quality Scorer.

Fast heuristic scorer that runs on every non-streaming response.
No LLM calls. Completes in < 1ms.

Score: 0.0 (very poor) → 1.0 (excellent)

Signals and weights:
  Length adequacy    0.25  — longer responses (up to 300 chars) score higher
  No repetition      0.20  — penalises repeated trigrams
  Not a refusal      0.20  — detects "I cannot / I'm unable" openers
  Not truncated      0.15  — response must end with proper punctuation
  Task-format match  0.20  — code responses should have code blocks, etc.
"""

from __future__ import annotations

import re
from collections import Counter

from a1.common.logging import get_logger

log = get_logger("healing.quality_scorer")

# Phrases that indicate a low-utility refusal response.
# Checked only in the first 200 characters.
REFUSAL_PHRASES: tuple[str, ...] = (
    "i cannot",
    "i'm unable to",
    "i can't help",
    "as an ai",
    "i don't have access",
    "i apologize, but",
    "sorry, i can't",
    "i'm not able to",
    "i am not able to",
    "i am unable to",
)

# Characters that count as valid sentence terminators (response is *not* truncated
# if its last character is in this set or the response ends with ``` or """)
_TERMINAL_CHARS: frozenset[str] = frozenset(".!?\"'`)]}")

# Task types that should produce code blocks
_CODE_TASK_TYPES: frozenset[str] = frozenset({"code", "infra", "security"})
# Task types that should produce paragraph text
_PROSE_TASK_TYPES: frozenset[str] = frozenset({"data", "audit", "writing"})


def score_response(response_text: str, task_type: str = "general") -> float:
    """Return a quality score 0.0–1.0 for a response.

    Fast heuristic — no LLM calls.  Should complete in < 1ms.

    Args:
        response_text: The full assistant response text.
        task_type:     One of the Atlas task types (chat, code, infra, …).

    Returns:
        Float in [0.0, 1.0] rounded to 3 decimal places.
    """
    if not response_text or not response_text.strip():
        return 0.0

    text = response_text.strip()
    lower = text.lower()

    # ── 1. Length adequacy (0–0.25) ──────────────────────────────────────────
    length_score = min(len(text), 300) / 300 * 0.25

    # ── 2. Repetition penalty (0–0.20) ───────────────────────────────────────
    words = lower.split()
    if len(words) > 10:
        trigrams = [tuple(words[i : i + 3]) for i in range(len(words) - 2)]
        counts = Counter(trigrams)
        repeated = sum(v - 1 for v in counts.values() if v > 1)
        repetition_ratio = min(repeated / max(len(trigrams), 1), 1.0)
        repeat_score = (1.0 - repetition_ratio) * 0.20
    else:
        repeat_score = 0.20  # short responses can't really be repetitive

    # ── 3. Refusal detection (0 or 0.20) ─────────────────────────────────────
    is_refusal = any(phrase in lower[:200] for phrase in REFUSAL_PHRASES)
    refusal_score = 0.0 if is_refusal else 0.20

    # ── 4. Truncation detection (0 or 0.15) ──────────────────────────────────
    last_char = text[-1]
    ends_with_code_fence = text.endswith("```")
    ends_with_triple_quote = text.endswith('"""') or text.endswith("'''")
    is_truncated = (
        len(text) > 200
        and last_char not in _TERMINAL_CHARS
        and not ends_with_code_fence
        and not ends_with_triple_quote
    )
    trunc_score = 0.0 if is_truncated else 0.15

    # ── 5. Task-format match (0.05–0.20) ─────────────────────────────────────
    has_code_block = "```" in text
    has_paragraphs = "\n\n" in text

    if task_type in _CODE_TASK_TYPES:
        format_score = 0.20 if has_code_block else 0.05
    elif task_type in _PROSE_TASK_TYPES:
        format_score = 0.20 if has_paragraphs else 0.10
    else:
        format_score = 0.20  # chat / general — no format requirement

    total = length_score + repeat_score + refusal_score + trunc_score + format_score
    return round(min(total, 1.0), 3)


async def score_and_store(
    response_text: str,
    task_type: str,
    message_id: str,
) -> float:
    """Compute quality score and persist as a QualitySignal row.

    Designed to be fire-and-forget via asyncio.create_task().
    All errors are swallowed so they never surface to the user.

    Returns:
        The computed score (useful if awaited directly in tests).
    """
    import uuid as _uuid

    from a1.db.engine import async_session
    from a1.db.models import QualitySignal

    score = score_response(response_text, task_type)

    try:
        async with async_session() as db:
            async with db.begin():
                signal = QualitySignal(
                    message_id=_uuid.UUID(message_id) if isinstance(message_id, str) else message_id,
                    signal_type="auto_eval",
                    value=score,
                    evaluator="heuristic_v1",
                )
                db.add(signal)
        log.debug(f"[quality-scorer] score={score:.3f} task={task_type} msg={message_id[:8]}")
    except Exception as exc:
        log.debug(f"[quality-scorer] DB write skipped: {exc}")

    return score
