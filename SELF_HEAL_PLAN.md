# Self-Heal Model — Conversation Improvement System

**Date:** 2026-04-30
**Status:** Ready for implementation
**Scope:** 5 layers — quality scoring → self-critique → feedback regeneration → health monitoring → training curation

---

## 0. Current Gaps (audited against live codebase)

| Gap | Evidence |
|---|---|
| Quality only measured for LOCAL vs Claude (distillation) | `_compute_similarity()` only called in `handle_dual_execution()` |
| Claude's OWN responses have no quality score | `QualitySignal` rows only written by Paperclip importer + feedback endpoint |
| Thumbs-down feedback stores a row but triggers NOTHING | `POST /conversations/{id}/feedback` writes `QualitySignal`, returns `{"ok": true}` — no follow-up |
| Training data is unfiltered — all Claude responses go in | `_check_and_trigger_training()` counts samples with no quality gate |
| No conversation-level health tracking | No table, no background monitor |
| No retry/escalation on a poor response | `CorePipeline` retries on PROVIDER FAILURE, not on quality failure |

**Existing infrastructure to build on:**
- `QualitySignal` model (`thumbs`, `score`, `auto_eval`, `comparison`) — already in DB
- `DualExecutionRecord.quality_score` field — exists, populated for distillation
- `_compute_similarity()` — Jaccard+ROUGE-L scorer in `auto_trainer.py`
- `CorePipelineResult.error` — hook point for quality gate
- WebSocket live-feed — already broadcasts events to dashboard
- `TaskTypeReadiness` lifecycle state machine — can consume quality signals

---

## 1. Architecture — 5 Layers

```
Request
  │
  ▼
CorePipeline.execute()
  │
  ├─► [Layer 1] Quality Scorer          ← fast heuristic, < 5ms, every response
  │       │  score < QUALITY_MIN_SCORE?
  │       ▼ yes (non-streaming only)
  ├─► [Layer 2] Self-Critique            ← Claude critiques + rewrites its own response
  │       │  store original, return improved
  │       ▼
  └─► Response delivered to user
          │
          ├─► [Layer 3] Feedback Handler ← user thumbs-down → regenerate + store training pair
          │
          └─► [Layer 4] Health Monitor   ← background task, scores each conversation
                                           flags: stuck / abandoned / low_quality
                  │
                  ▼
          [Layer 5] Training Curation    ← only quality-gated records enter fine-tuning
```

---

## 2. Layer 1 — Response Quality Scorer

### File: `src/a1/healing/quality_scorer.py`

Fast, zero-LLM scorer. Runs as background `asyncio.create_task()` after every response.
Score: **0.0–1.0**. Stored in `quality_signals` as `signal_type="auto_eval"`.

**Heuristic signals and weights:**

| Signal | How computed | Weight |
|---|---|---|
| Length adequacy | `min(len(response), 200) / 200` | 0.25 |
| No-repetition | `1 - (repeated_ngram_ratio)` | 0.20 |
| Not a refusal | `0.0` if response starts with refusal phrase | 0.20 |
| Not truncated | `0.0` if ends mid-sentence without punctuation | 0.15 |
| Task-format match | Code task → has code block; analysis → has paragraphs | 0.20 |

**Refusal phrases to detect** (returns score=0.0 for length signal):
`"i cannot", "i'm unable", "i can't help", "as an ai", "i don't have access"`,
`"i apologize", "sorry, i can't"` — these indicate low utility responses.

**Truncation detection:**
Response ends without `.`, `!`, `?`, `"""`, ` ``` ` and is > 200 chars long.

**Implementation:**

```python
# src/a1/healing/quality_scorer.py

import re
from collections import Counter

REFUSAL_PHRASES = [
    "i cannot", "i'm unable to", "i can't help",
    "as an ai", "i don't have access", "i apologize, but",
    "sorry, i can't", "i'm not able to",
]

TERMINAL_CHARS = {'.', '!', '?', '"', "'", '`'}


def score_response(response_text: str, task_type: str = "general") -> float:
    """Return a quality score 0.0–1.0 for a response.

    Fast heuristic — no LLM calls. Should complete in < 1ms.
    """
    if not response_text or not response_text.strip():
        return 0.0

    text = response_text.strip()
    lower = text.lower()

    # 1. Length adequacy (0–0.25)
    length_score = min(len(text), 300) / 300 * 0.25

    # 2. Repetition penalty (0–0.20)
    words = lower.split()
    if len(words) > 10:
        trigrams = [tuple(words[i:i+3]) for i in range(len(words) - 2)]
        counts = Counter(trigrams)
        repeated = sum(v - 1 for v in counts.values() if v > 1)
        repetition_ratio = min(repeated / len(trigrams), 1.0)
        repeat_score = (1 - repetition_ratio) * 0.20
    else:
        repeat_score = 0.20

    # 3. Refusal detection (0 or 0.20)
    is_refusal = any(phrase in lower[:200] for phrase in REFUSAL_PHRASES)
    refusal_score = 0.0 if is_refusal else 0.20

    # 4. Truncation detection (0 or 0.15)
    last_char = text[-1] if text else ''
    is_truncated = (
        len(text) > 200
        and last_char not in TERMINAL_CHARS
        and not text.endswith("```")
    )
    trunc_score = 0.0 if is_truncated else 0.15

    # 5. Task-format match (0 or 0.20)
    has_code_block = "```" in text
    has_paragraphs = text.count('\n\n') >= 1
    if task_type in ("code", "infra", "security"):
        format_score = 0.20 if has_code_block else 0.05
    elif task_type in ("data", "audit", "writing"):
        format_score = 0.20 if has_paragraphs else 0.10
    else:
        format_score = 0.20  # chat/general — no format requirement

    total = length_score + repeat_score + refusal_score + trunc_score + format_score
    return round(min(total, 1.0), 3)


async def score_and_store(
    response_text: str,
    task_type: str,
    message_id: str,
    db_session_factory,  # async_session callable
) -> float:
    """Score response and store as QualitySignal. Returns score."""
    from a1.db.models import QualitySignal
    from a1.db.engine import async_session
    import uuid

    score = score_response(response_text, task_type)

    try:
        async with async_session() as session:
            async with session.begin():
                signal = QualitySignal(
                    message_id=uuid.UUID(message_id),
                    signal_type="auto_eval",
                    value=score,
                    evaluator="heuristic_v1",
                )
                session.add(signal)
    except Exception:
        pass  # non-critical

    return score
```

**Integration point in `CorePipeline.execute()`:**
After the DB persist block (after `message_id` is known), add:

```python
# Fire-and-forget quality scoring
if result.message_id and result.assistant_text:
    import asyncio
    from a1.healing.quality_scorer import score_and_store
    asyncio.create_task(
        score_and_store(
            result.assistant_text,
            result.task_type,
            result.message_id,
            async_session,
        )
    )
    result.quality_score = score_response(result.assistant_text, result.task_type)
```

Add `quality_score: float = 0.0` to `CorePipelineResult`.

---

## 3. Layer 2 — Self-Critique & Regeneration

### File: `src/a1/healing/self_critique.py`

When `quality_score < settings.quality_min_score` AND the request is non-streaming,
Claude critiques and rewrites its own response before it reaches the user.

**Config additions** (`config/settings.py`):
```python
self_critique_enabled: bool = True
quality_min_score: float = 0.40     # below this → trigger self-critique
quality_critique_model: str = "claude-haiku-4-5"  # fast, cheap
```

**Self-critique prompt template:**

```
You generated the following response to a user's request. It has been flagged as
potentially low-quality. Please:
1. Identify the main weaknesses (1-2 sentences max)
2. Generate a significantly improved version

=== ORIGINAL USER REQUEST ===
{user_message}

=== YOUR ORIGINAL RESPONSE ===
{original_response}

=== TASK TYPE ===
{task_type}

Respond with ONLY the improved response — no preamble, no meta-commentary.
```

**Implementation:**

```python
# src/a1/healing/self_critique.py

from a1.common.logging import get_logger

log = get_logger("healing.self_critique")

CRITIQUE_PROMPT = """\
You generated the following response to a user request. It has been flagged as \
potentially low-quality. Generate a significantly improved version.

Rules:
- Respond with ONLY the improved response (no meta-commentary)
- Make it more complete, accurate, and useful
- Match the task type: {task_type}

=== USER REQUEST ===
{user_message}

=== YOUR ORIGINAL RESPONSE ===
{original_response}
"""


async def self_critique(
    user_message: str,
    original_response: str,
    task_type: str,
    provider,          # LLMProvider instance
    model: str,        # e.g. "claude-haiku-4-5"
    max_tokens: int = 1500,
) -> str | None:
    """Ask Claude to critique and rewrite original_response.

    Returns improved response text, or None if critique failed.
    """
    from a1.proxy.request_models import ChatCompletionRequest

    prompt = CRITIQUE_PROMPT.format(
        user_message=user_message[:2000],
        original_response=original_response[:3000],
        task_type=task_type,
    )

    try:
        req = ChatCompletionRequest(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,  # lower temp → more focused improvement
        )
        resp = await provider.complete(req)
        improved = resp.choices[0].message.content if resp.choices else None
        if improved and len(improved.strip()) > 20:
            log.info(f"Self-critique improved response (task={task_type})")
            return improved.strip()
    except Exception as e:
        log.warning(f"Self-critique failed: {e}")

    return None
```

**Integration in `CorePipeline.execute()`** — add after the response is generated,
before DB persist, **non-streaming only**:

```python
# Self-critique gate (non-streaming, quality below threshold)
if (
    settings.self_critique_enabled
    and not inp.stream
    and result.assistant_text
    and not result.cache_hit
    and not result.is_local        # only critique external provider responses
):
    from a1.healing.quality_scorer import score_response
    from a1.healing.self_critique import self_critique

    q_score = score_response(result.assistant_text, result.task_type)
    if q_score < settings.quality_min_score:
        log.info(
            f"[self-heal] Quality score {q_score} < {settings.quality_min_score}"
            f" for task={result.task_type} — triggering self-critique"
        )
        critique_provider = provider_registry.get_provider("claude-cli")
        if critique_provider:
            improved = await self_critique(
                user_message=inp.raw_user_input,
                original_response=result.assistant_text,
                task_type=result.task_type,
                provider=critique_provider,
                model=settings.quality_critique_model,
            )
            if improved:
                result.original_response = result.assistant_text  # save original
                result.assistant_text = improved
                result.self_healed = True
                log.info("[self-heal] Response replaced with improved version")
```

Add to `CorePipelineResult`:
```python
quality_score: float = 0.0
self_healed: bool = False
original_response: str | None = None  # pre-critique text, for audit
```

Add to `RoutingDecision` model (new column, needs migration):
```python
self_healed: Mapped[bool] = mapped_column(Boolean, default=False)
heal_score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
```

**Migration** (`alembic/versions/xxxx_add_self_heal_fields.py`):
```python
def upgrade():
    op.add_column('routing_decisions', sa.Column('self_healed', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('routing_decisions', sa.Column('heal_score_before', sa.Float(), nullable=True))
```

---

## 4. Layer 3 — Feedback-Triggered Improvement

### File: `src/a1/healing/feedback_handler.py`

When a user gives thumbs-down (`value = -1`), the system automatically:
1. Generates an improved response using the strongest available Claude model
2. Stores the better response as a new message in the conversation
3. Creates a high-quality training pair immediately
4. Broadcasts a WebSocket notification to the dashboard

**Implementation:**

```python
# src/a1/healing/feedback_handler.py

from a1.common.logging import get_logger
log = get_logger("healing.feedback")

IMPROVE_PROMPT = """\
The user was unsatisfied with the following assistant response. Generate a \
significantly better response to the original user message.

Requirements:
- More complete and accurate than the original
- Directly answers what the user asked
- Appropriate for task type: {task_type}

=== ORIGINAL USER MESSAGE ===
{user_message}

=== UNSATISFACTORY RESPONSE ===
{bad_response}

Respond with ONLY the improved response.
"""


async def handle_thumbs_down(
    conversation_id: str,
    message_id: str,
    db,  # AsyncSession
):
    """Triggered when a user submits thumbs-down on an assistant message.

    1. Retrieves conversation context
    2. Generates improved response
    3. Stores improved response as new Message (source="self_heal")
    4. Creates DualExecutionRecord pair for training
    5. Broadcasts WebSocket event
    """
    from a1.db.repositories import ConversationRepo, MessageRepo
    from a1.db.models import Message, DualExecutionRecord, QualitySignal
    from a1.providers.registry import provider_registry
    from a1.proxy.request_models import ChatCompletionRequest
    from a1.dashboard.router import broadcast_event
    from config.settings import settings
    import uuid

    # Load conversation + flagged message
    msg_repo = MessageRepo(db)
    message = await msg_repo.get(message_id)
    if not message or message.role != "assistant":
        return

    conv_repo = ConversationRepo(db)
    conversation = await conv_repo.get(conversation_id)
    if not conversation:
        return

    # Get the user turn preceding this assistant message
    messages = await msg_repo.list_by_conversation(conversation_id)
    user_msg = None
    for i, m in enumerate(messages):
        if str(m.id) == message_id and i > 0:
            if messages[i - 1].role == "user":
                user_msg = messages[i - 1].content
            break

    if not user_msg or not message.content:
        return

    # Generate improved response
    provider = provider_registry.get_provider("claude-cli")
    if not provider:
        return

    task_type = conversation.task_type or "general"
    prompt = IMPROVE_PROMPT.format(
        user_message=user_msg[:2000],
        bad_response=message.content[:3000],
        task_type=task_type,
    )

    try:
        req = ChatCompletionRequest(
            model=settings.distillation_claude_model,  # best model
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.4,
        )
        resp = await provider.complete(req)
        improved_text = resp.choices[0].message.content if resp.choices else None
    except Exception as e:
        log.warning(f"[feedback-heal] Generation failed: {e}")
        return

    if not improved_text or len(improved_text.strip()) < 20:
        return

    # Store as new Message
    async with db.begin():
        new_msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=improved_text.strip(),
            sequence=(message.sequence or 0) + 1,
            metadata={"source": "self_heal", "original_message_id": message_id},
        )
        db.add(new_msg)

        # Mark original message as healed
        message.metadata = {**(message.metadata or {}), "self_healed": True}

        # Create quality signal on original (negative)
        neg_signal = QualitySignal(
            message_id=message.id,
            signal_type="auto_eval",
            value=0.1,
            evaluator="user_thumbsdown",
        )
        db.add(neg_signal)

    await db.commit()
    await db.refresh(new_msg)

    # Broadcast to dashboard
    await broadcast_event({
        "type": "self_heal_complete",
        "conversation_id": conversation_id,
        "original_message_id": message_id,
        "improved_message_id": str(new_msg.id),
        "task_type": task_type,
    })

    log.info(
        f"[feedback-heal] Improved message {message_id} → {new_msg.id}"
        f" for conv {conversation_id}"
    )
```

**Integration in `conversations_router.py`** — update the feedback endpoint:

```python
@router.post("/conversations/{conv_id}/feedback")
async def add_feedback(
    conv_id: str,
    message_id: str,
    value: int,         # +1 or -1
    signal_type: str = "thumbs",
    db: AsyncSession = Depends(get_db),
):
    # ... existing logic to store QualitySignal ...

    # NEW: trigger improvement on thumbs-down
    if value <= -1 and settings.feedback_regen_enabled:
        import asyncio
        from a1.healing.feedback_handler import handle_thumbs_down
        asyncio.create_task(handle_thumbs_down(conv_id, message_id, db))

    return {"ok": True}
```

**New config keys:**
```python
feedback_regen_enabled: bool = True  # trigger regeneration on thumbs-down
```

---

## 5. Layer 4 — Conversation Health Monitor

### File: `src/a1/healing/conversation_monitor.py`

Background `asyncio` task, runs every `health_monitor_interval_seconds` (default 300s).
Scans conversations from the last 24h. Writes health scores to a new table.

**New DB table** (`conversation_health`):
```python
class ConversationHealth(Base):
    __tablename__ = "conversation_health"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("conversations.id"), nullable=False, unique=True
    )
    health_score: Mapped[float] = mapped_column(Float, default=1.0)     # 0.0–1.0
    flags: Mapped[dict] = mapped_column(JSON, default=dict)              # {"stuck": true, ...}
    avg_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

**Health score formula:**
```
health_score = (
    0.40 * avg_quality_signal          # from QualitySignal table
    + 0.30 * (1 - stuck_penalty)       # 0.0 if stuck, 1.0 if not
    + 0.20 * (1 - abandonment_penalty) # 0.0 if abandoned, 1.0 if resolved
    + 0.10 * (1 - length_penalty)      # penalise very long unresolved convs
)
```

**Flags detected:**
- `stuck`: same user message appears 3+ times (verbatim or >80% Jaccard similarity)
- `abandoned`: last message is from user (no assistant response followed)
- `low_quality`: avg auto_eval quality_signal < 0.35
- `self_healed`: at least one message was regenerated via feedback

**Background task registration** (`src/a1/app.py`):
```python
@app.on_event("startup")
async def start_health_monitor():
    from a1.healing.conversation_monitor import run_health_monitor
    asyncio.create_task(run_health_monitor())
```

**Monitor loop skeleton:**
```python
async def run_health_monitor():
    while True:
        try:
            await _scan_recent_conversations()
        except Exception as e:
            log.warning(f"[health-monitor] Scan failed: {e}")
        await asyncio.sleep(settings.health_monitor_interval_seconds)
```

---

## 6. Layer 5 — Quality-Gated Training Data

### File modified: `src/a1/training/auto_trainer.py`

**Change 1:** Filter distillation training samples by quality score.

In `_check_and_trigger_training()`, add a WHERE clause when counting usable samples:

```python
# Only count records where quality_score >= threshold as usable training samples
usable_count = await session.scalar(
    select(func.count(DualExecutionRecord.id))
    .where(
        DualExecutionRecord.task_type == task_type,
        DualExecutionRecord.quality_score >= settings.distillation_quality_threshold,
    )
)
# Use usable_count (not total count) to decide whether to trigger training
if usable_count >= settings.distillation_min_samples:
    await _trigger_training(session, task_type)
```

**Change 2:** Self-heal regenerated responses get priority weighting.

When building the training dataset, add a `weight` column — self-healed pairs get `weight=2.0`
vs standard `weight=1.0`. This doubles their influence on the fine-tuned model.

**Change 3:** Negative pair mining.

For records with `quality_score < 0.2`, create a "contrast pair" — the bad response
paired with a self-critique improved version — stored as `DualExecutionRecord` with
`is_contrast_pair=True` flag (new column, nullable). Contrast pairs teach the model
what NOT to produce.

```python
# New column on DualExecutionRecord:
is_contrast_pair: Mapped[bool] = mapped_column(Boolean, default=False)
contrast_improved: Mapped[str | None] = mapped_column(Text, nullable=True)
```

---

## 7. Database Migration

Single migration file: `alembic/versions/XXXX_add_self_heal.py`

```python
def upgrade():
    # routing_decisions — self-heal tracking
    op.add_column('routing_decisions',
        sa.Column('self_healed', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('routing_decisions',
        sa.Column('heal_score_before', sa.Float(), nullable=True))

    # dual_execution_records — negative pair mining
    op.add_column('dual_execution_records',
        sa.Column('is_contrast_pair', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('dual_execution_records',
        sa.Column('contrast_improved', sa.Text(), nullable=True))

    # conversation_health — new table
    op.create_table(
        'conversation_health',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('conversation_id', sa.UUID(),
            sa.ForeignKey('conversations.id', ondelete='CASCADE'),
            nullable=False, unique=True),
        sa.Column('health_score', sa.Float(), nullable=False, default=1.0),
        sa.Column('flags', sa.JSON(), nullable=False, default={}),
        sa.Column('avg_quality', sa.Float(), nullable=True),
        sa.Column('turn_count', sa.Integer(), default=0),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_conv_health_conv', 'conversation_health', ['conversation_id'])
```

---

## 8. New Settings (`config/settings.py`)

```python
# Self-Heal
self_critique_enabled: bool = True
quality_min_score: float = 0.40           # threshold to trigger self-critique
quality_critique_model: str = "claude-haiku-4-5"  # fast, cheap critique model
feedback_regen_enabled: bool = True       # thumbs-down triggers regeneration
health_monitor_interval_seconds: int = 300  # how often to scan conversations
```

---

## 9. New Files Summary

```
src/a1/healing/
├── __init__.py
├── quality_scorer.py          Layer 1 — heuristic scoring (score_response, score_and_store)
├── self_critique.py           Layer 2 — self-critique prompt + provider call
├── conversation_monitor.py    Layer 4 — background health scan loop
└── feedback_handler.py        Layer 3 — thumbs-down triggered regeneration
```

---

## 10. Modified Files Summary

| File | Change |
|---|---|
| `src/a1/proxy/core_pipeline.py` | Call quality scorer + self-critique gate after generation |
| `src/a1/db/models.py` | Add `ConversationHealth` table; add `self_healed`, `heal_score_before` to `RoutingDecision`; add `is_contrast_pair`, `contrast_improved` to `DualExecutionRecord` |
| `src/a1/dashboard/conversations_router.py` | Feedback endpoint fires `handle_thumbs_down` task |
| `src/a1/training/auto_trainer.py` | Quality-gate training data; contrast pair mining |
| `src/a1/app.py` | Register `run_health_monitor()` at startup |
| `config/settings.py` | Add 5 new self-heal settings |
| `alembic/versions/` | New migration for schema changes |

---

## 11. Dashboard Changes

### Conversations list (`Conversations.tsx`)
Add a `health_score` column showing a colored badge:
- 🟢 ≥ 0.7 — Healthy
- 🟡 0.4–0.69 — Needs attention
- 🔴 < 0.4 — Poor (flagged)

Fetch health scores via a new endpoint: `GET /admin/conversations/health` that
joins `conversation_health` with `conversations`.

### Conversation detail (`ConversationDetail.tsx`)
- Show `quality_score` per assistant message (small badge, 0.0–1.0)
- Show `self_healed` badge on regenerated messages
- "Regenerate" button on assistant messages → calls
  `POST /admin/conversations/{id}/messages/{msg_id}/regenerate`
  (new endpoint that calls `handle_thumbs_down` directly, no feedback score needed)

### Overview page addition
In the KPI row, add a "Healed" card: count of `routing_decisions WHERE self_healed=true`.
Pull from `db_stats` (add to overview endpoint).

### New page: `Healing.tsx` (optional, Phase 2)
Full self-heal monitoring dashboard:
- Quality score distribution histogram
- Self-critique rate trend (% of requests triggered per day)
- Conversation health heatmap (day × health bucket)
- Top self-healed conversations (most improved)
- Training data quality gate stats (accepted vs filtered samples)

---

## 12. Implementation Order

### Phase 1 — Core (3–4 hours, immediate value)
1. `src/a1/healing/__init__.py` + `quality_scorer.py`
2. DB migration (add columns + `conversation_health` table)
3. `CorePipeline` — fire quality scorer as background task
4. Update `CorePipelineResult` and `RoutingDecision` DB persist to store `self_healed`
5. `self_critique.py` + CorePipeline gate (non-streaming only)
6. Settings additions

### Phase 2 — Feedback Loop (1–2 hours)
7. `feedback_handler.py`
8. Update `conversations_router.py` feedback endpoint
9. Add `regenerate` endpoint for manual triggering
10. `ConversationDetail.tsx` — regenerate button + quality badges

### Phase 3 — Health Monitor + Training Curation (2–3 hours)
11. `conversation_monitor.py` + `ConversationHealth` model
12. App startup hook
13. `auto_trainer.py` — quality gate + contrast pairs
14. `Conversations.tsx` — health badge
15. Overview `db_stats` — add `self_healed_count`

---

## 13. Expected Impact

| Metric | Before | After (estimated) |
|---|---|---|
| % responses quality-scored | 0% | 100% |
| % responses self-critiqued | 0% | ~15% (those below 0.40 threshold) |
| Thumbs-down → improvement lag | Never | < 30 seconds |
| Training data quality gate | None | Only ≥ 0.50 quality scores |
| Conversation health visibility | None | Per-conversation health badge |
| Distillation sample efficiency | 100% of samples used | ~70% (higher quality) |

The quality gate on training data will make distillation MORE efficient: fewer but
better samples → better fine-tuned models → higher local handoff % → lower cost.

---

## 14. Testing Plan

- [ ] `score_response("", "chat")` → 0.0
- [ ] `score_response("I cannot help with that.", "chat")` → low (≤ 0.20, refusal detected)
- [ ] `score_response("Here is a complete answer...\n\n", "code")` → medium (no code block)
- [ ] `score_response("Here is the answer:\n```python\nx=1\n```\n", "code")` → high (≥ 0.70)
- [ ] Self-critique: send a 3-word response, verify pipeline replaces it
- [ ] Feedback: POST thumbs-down, verify new message appears in conversation
- [ ] Health monitor: conversation with repeated user message → `stuck=True` flag
- [ ] Training gate: `quality_score < 0.5` records excluded from training count
- [ ] Migration: `alembic upgrade head` with no errors
- [ ] Build: `npm run build` with no TypeScript errors (after Dashboard changes)
- [ ] 105 existing tests still pass
