import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON as GenericJSON  # noqa: N811
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from a1.common.tz import now_ist as _now_ist
from config.settings import settings

# Use JSONB for PostgreSQL, JSON for SQLite
if "sqlite" in settings.database_url:
    import uuid as _uuid_mod

    from sqlalchemy import TypeDecorator

    JSONB = GenericJSON

    class SQLiteUUID(TypeDecorator):
        """UUID stored as string in SQLite, auto-converts UUID objects."""

        impl = String(36)
        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is not None:
                return str(value)
            return value

        def process_result_value(self, value, dialect):
            if value is not None:
                return _uuid_mod.UUID(value) if not isinstance(value, _uuid_mod.UUID) else value
            return value

    def UUID(as_uuid=True):  # noqa: N802
        return SQLiteUUID()
else:
    from sqlalchemy.dialects.postgresql import JSONB, UUID

from a1.db.engine import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(String(512), unique=True, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # proxy, import:paperclip, etc.
    user_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.sequence", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence"),
        CheckConstraint("role IN ('system', 'user', 'assistant', 'tool')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    routing_decision: Mapped["RoutingDecision | None"] = relationship(
        back_populates="message", uselist=False
    )
    quality_signals: Mapped[list["QualitySignal"]] = relationship(back_populates="message")


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routing_decisions.id"), nullable=True
    )
    is_local: Mapped[bool] = mapped_column(default=False)
    api_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # Self-heal tracking
    self_healed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    heal_score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Vertex AI / Gemini web search grounding
    web_search_grounded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    grounding_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    message: Mapped[Message] = relationship(back_populates="routing_decision")


class QualitySignal(Base):
    __tablename__ = "quality_signals"
    __table_args__ = (
        CheckConstraint("signal_type IN ('thumbs', 'score', 'auto_eval', 'comparison')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False
    )
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    evaluator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    message: Mapped[Message] = relationship(back_populates="quality_signals")


class ModelPerformance(Base):
    __tablename__ = "model_performance"
    __table_args__ = (UniqueConstraint("task_type", "provider", "model", "period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    period: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    avg_quality: Mapped[float] = mapped_column(Float, nullable=False)
    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)


class TrainingRun(Base):
    __tablename__ = "training_runs"
    __table_args__ = (CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    base_model: Mapped[str] = mapped_column(String(256), nullable=False)
    dataset_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    ollama_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


class User(Base):
    """A named user who owns API keys and has usage tracked against them."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(32), default="developer")  # admin | developer | viewer
    rate_limit: Mapped[int] = mapped_column(
        Integer, default=60
    )  # requests/min applied to all their keys
    monthly_token_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), default="developer")  # admin | developer | viewer
    is_active: Mapped[bool] = mapped_column(default=True)
    rate_limit: Mapped[int] = mapped_column(Integer, default=60)  # requests per minute
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    user: Mapped["User | None"] = relationship(back_populates="api_keys")


class ProviderAccount(Base):
    """Multiple API keys per provider for load balancing and failover."""

    __tablename__ = "provider_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # anthropic, openai, vertex
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher = preferred
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    current_month_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


class UsageRecord(Base):
    """Per-request usage tracking for analytics and billing."""

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_accounts.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    is_local: Mapped[bool] = mapped_column(default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    equivalent_external_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False, default=0
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[bool] = mapped_column(default=False)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


class UsageHourlyRollup(Base):
    """Pre-aggregated hourly usage for fast time-series queries."""

    __tablename__ = "usage_hourly_rollups"
    __table_args__ = (UniqueConstraint("api_key_hash", "provider", "model", "is_local", "hour"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    is_local: Mapped[bool] = mapped_column(default=False)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    equivalent_external_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False, default=0
    )
    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    p50_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


# --- Distillation Pipeline ---


class DualExecutionRecord(Base):
    """Tracks dual execution: Claude response + local model response for the same request."""

    __tablename__ = "dual_execution_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Request info
    request_messages: Mapped[dict] = mapped_column(JSONB, nullable=False)  # original messages

    # Claude (teacher) side
    claude_model: Mapped[str] = mapped_column(String(128), nullable=False)
    claude_response: Mapped[str] = mapped_column(Text, nullable=False)
    claude_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    claude_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    claude_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Local (student) side
    local_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Comparison scoring
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1 cosine
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # auto-evaluated
    used_for_training: Mapped[bool] = mapped_column(default=False)

    # Negative pair mining (contrast pairs)
    is_contrast_pair: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    contrast_improved: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Web-grounded RAG training data
    # True when the teacher answer was grounded in web search results.
    # RAG pairs are stored for fine-tuning the local model to cite sources,
    # but are excluded from fast-changing fact training to avoid stale data.
    has_web_context: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    web_search_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


class TaskTypeReadiness(Base):
    """Per-task-type training readiness and graduated handoff tracking.

    Lifecycle state machine:
      LEARNING → TRAINING → EVALUATING → CANARY → GRADUATED → RETIRED

    LEARNING:   Collecting teacher/student pairs. 100% 3rd-party traffic.
    TRAINING:   QLoRA fine-tuning job running (TrainingRun record active).
    EVALUATING: lm-eval benchmarks running post-training.
    CANARY:     1–90% local traffic, promoted gradually if quality holds.
    GRADUATED:  Local model handles max_local_pct of traffic autonomously.
    RETIRED:    Superseded by a newer training run.
    """

    __tablename__ = "task_type_readiness"
    __table_args__ = (
        UniqueConstraint("task_type"),
        CheckConstraint(
            "lifecycle_state IN "
            "('learning','training','evaluating','canary','graduated','retired')",
            name="chk_lifecycle_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Lifecycle state machine
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="learning")

    # Sample collection
    claude_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    training_threshold: Mapped[int] = mapped_column(Integer, default=100)

    # Quality tracking
    local_avg_quality: Mapped[float] = mapped_column(Float, default=0.0)
    quality_gate_score: Mapped[float] = mapped_column(
        Float, default=0.75
    )  # min for canary→graduated
    lm_eval_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Traffic routing
    local_handoff_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 to 1.0
    max_local_pct: Mapped[float] = mapped_column(Float, default=0.9)  # per-task cap

    # Model tracking
    best_local_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_training_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Lifecycle timestamps
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canary_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    graduated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_regression_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Argilla annotation gate
    argilla_review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Values: None | "pending_argilla_review" | "approved" | "rejected"
    argilla_batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )


# ---------------------------------------------------------------------------
# Organizational Platform — P1 schema (workspaces / teams / channels)
# ---------------------------------------------------------------------------


class Workspace(Base):
    """Top-level organizational unit. All teams, agents, and apps belong to one."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    computer_use_allowed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    teams: Mapped[list["Team"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Team(Base):
    """A group within a workspace with a shared Atlas model and persona."""

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    atlas_model: Mapped[str] = mapped_column(String(64), default="atlas-plan")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    workspace: Mapped[Workspace] = relationship(back_populates="teams")
    channels: Mapped[list["Channel"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class Channel(Base):
    """A conversation channel within a team (e.g., #engineering, #data-qa)."""

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("team_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    team: Mapped[Team] = relationship(back_populates="channels")


class ConversationHealth(Base):
    """Per-conversation health score computed by the background health monitor.

    Updated every ``health_monitor_interval_seconds`` (default 300s).
    health_score: 0.0 (poor) → 1.0 (healthy)
    flags: JSON object, e.g. {"stuck": true, "abandoned": false, "low_quality": true}
    """

    __tablename__ = "conversation_health"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    health_score: Mapped[float] = mapped_column(Float, default=1.0)
    flags: Mapped[dict] = mapped_column(JSONB, default=dict)
    avg_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


# ---------------------------------------------------------------------------
# Indexes
Index("ix_dual_exec_task_type", DualExecutionRecord.task_type)
Index("ix_dual_exec_created", DualExecutionRecord.created_at)
Index("ix_task_readiness_task", TaskTypeReadiness.task_type)
Index("ix_messages_conversation_seq", Message.conversation_id, Message.sequence)
Index("ix_routing_decisions_created", RoutingDecision.created_at)
Index("ix_routing_decisions_task_type", RoutingDecision.task_type)
Index("ix_quality_signals_message", QualitySignal.message_id)
Index("ix_conv_health_conversation", ConversationHealth.conversation_id)
Index("ix_provider_accounts_provider", ProviderAccount.provider)
Index("ix_usage_records_created", UsageRecord.created_at)
Index("ix_usage_records_api_key_created", UsageRecord.api_key_hash, UsageRecord.created_at)
Index("ix_usage_records_provider_model", UsageRecord.provider, UsageRecord.model)
Index("ix_usage_hourly_hour", UsageHourlyRollup.hour)
Index("ix_workspaces_slug", Workspace.slug)
Index("ix_teams_workspace", Team.workspace_id)
Index("ix_channels_team", Channel.team_id)

# ---------------------------------------------------------------------------
# Organizational Platform — P2 schema (agents / applications)
# ---------------------------------------------------------------------------


class Application(Base):
    """A packaged AI deployment: system prompt + tools + Atlas model + API key.

    Teams deploy applications to give end-users access to a focused AI persona
    (e.g. "Code Reviewer Bot", "Data Q&A Assistant").
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    atlas_model: Mapped[str] = mapped_column(String(64), default="atlas-plan")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[dict] = mapped_column(JSONB, default=list)  # list of tool name strings
    agent_pool: Mapped[dict] = mapped_column(JSONB, default=list)  # list of agent UUIDs
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=60)
    app_settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )

    agents: Mapped[list["Agent"]] = relationship(
        "Agent",
        primaryjoin="Application.id == Agent.app_id",
        foreign_keys="Agent.app_id",
        back_populates="application",
        cascade="all, delete-orphan",
    )


class Agent(Base):
    """A named AI agent: Atlas model + persona + tool manifest + memory config.

    Agents can be standalone (direct API use), channel-bound (team chat),
    or application-bound (part of an Application's agent pool).
    Supports hierarchical agent trees via parent_id for CEO/Manager/Worker patterns.
    """

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    app_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # slug: "data-analyst-bot"
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    atlas_model: Mapped[str] = mapped_column(String(64), default="atlas-plan")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[dict] = mapped_column(JSONB, default=list)  # list of tool name strings
    memory_config: Mapped[dict] = mapped_column(
        JSONB, default=dict
    )  # {"type": "sliding_window", "limit": 20}
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active"
    )  # active | paused | terminated
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )

    application: Mapped["Application | None"] = relationship(
        "Application", foreign_keys=[app_id], back_populates="agents"
    )
    children: Mapped[list["Agent"]] = relationship(
        "Agent", foreign_keys=[parent_id], back_populates="parent"
    )
    parent: Mapped["Agent | None"] = relationship(
        "Agent", foreign_keys=[parent_id], back_populates="children", remote_side="Agent.id"
    )
    executions: Mapped[list["AgentExecution"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentMessage(Base):
    """Agent-to-agent message queue for orchestration and delegation."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','completed','failed')", name="chk_amsg_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    to_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentExecution(Base):
    """Audit log of every agent invocation: task, result, cost, latency."""

    __tablename__ = "agent_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    agent: Mapped[Agent] = relationship(back_populates="executions")


# ── Provisioning: OneDesk / platform-to-platform tenant keys ─────────────────


class AtlasApiKey(Base):
    """Tenant-specific API keys provisioned via the OneDesk provisioning API.

    Raw keys are NEVER stored — only the SHA-256 hash and a display prefix.
    """

    __tablename__ = "atlas_api_keys"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled','revoked')", name="chk_atlas_key_status"),
        Index("ix_atlas_api_keys_tenant_id", "tenant_id"),
        Index("ix_atlas_api_keys_key_hash", "key_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenant identity (from OneDesk)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tenant_name: Mapped[str] = mapped_column(String(256), nullable=False)
    tenant_owner_email: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="onedesk")  # which platform provisioned
    # Key material — raw key NEVER stored
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "sk-atlas-a1b2c3"
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)  # SHA-256
    # Lifecycle
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled | revoked
    default_model: Mapped[str] = mapped_column(String(128), default="Atlas")
    base_url: Mapped[str] = mapped_column(String(512), default="https://atlas.alpheric.ai/v1")
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Usage counters (incremented on each chat completion)
    requests_total: Mapped[int] = mapped_column(Integer, default=0)
    tokens_total: Mapped[int] = mapped_column(Integer, default=0)
    # Flexible metadata blob (reason for rotation, notes, etc.)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ProvisioningAuditLog(Base):
    """Immutable audit trail for every provisioning action.

    No secrets stored — key_hash is safe to log; raw keys are never written.
    """

    __tablename__ = "provisioning_audit_logs"
    __table_args__ = (
        Index("ix_prov_audit_tenant_id", "tenant_id"),
        Index("ix_prov_audit_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    alpheric_key_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # UUID as str
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # provisioned | rotated | disabled | status_checked | invalid_token | chat_completion
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success | failure
    safe_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256 of client IP
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


# ---------------------------------------------------------------------------
# Organizational Platform — P3 schema (task plans / channel members)
# ---------------------------------------------------------------------------


class TaskPlan(Base):
    """A multi-step plan decomposed by atlas-plan (CEO) into an execution tree.

    The decomposed field is a JSON tree of:
    [{"task": str, "agent_name": str, "dependencies": [int], "status": str, "result": str}]
    """

    __tablename__ = "task_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planning','executing','completed','failed','cancelled')",
            name="chk_plan_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    root_task: Mapped[str] = mapped_column(Text, nullable=False)
    decomposed: Mapped[dict] = mapped_column(JSONB, default=list)  # tree of subtasks
    status: Mapped[str] = mapped_column(String(16), default="planning")
    executor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_completed: Mapped[int] = mapped_column(Integer, default=0)
    steps_total: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChannelMember(Base):
    """Membership record linking users to channels."""

    __tablename__ = "channel_members"
    __table_args__ = (
        UniqueConstraint("channel_id", "user_id"),
        CheckConstraint("role IN ('admin','member','viewer')", name="chk_member_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


# P2 Indexes
Index("ix_applications_workspace", Application.workspace_id)
Index("ix_agents_workspace", Agent.workspace_id)
Index("ix_agents_app", Agent.app_id)
Index("ix_agent_messages_to", AgentMessage.to_agent_id)
Index("ix_agent_messages_status", AgentMessage.status)
Index("ix_agent_executions_agent", AgentExecution.agent_id)

# P3 Indexes
Index("ix_task_plans_workspace", TaskPlan.workspace_id)
Index("ix_task_plans_status", TaskPlan.status)
Index("ix_channel_members_channel", ChannelMember.channel_id)
Index("ix_channel_members_user", ChannelMember.user_id)


# ---------------------------------------------------------------------------
# P4: Computer Usage + Notebook
# ---------------------------------------------------------------------------


class ComputerSession(Base):
    """Audit trail for browser/desktop automation sessions."""

    __tablename__ = "computer_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','failed','terminated')",
            name="chk_computer_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    session_type: Mapped[str] = mapped_column(String(16), default="browser")  # browser | desktop
    start_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    screenshot_count: Mapped[int] = mapped_column(Integer, default=0)
    actions_taken: Mapped[dict] = mapped_column(JSONB, default=list)  # [{action, ts, detail}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notebook(Base):
    """AI-assisted notebook (Jupyter-compatible cell model)."""

    __tablename__ = "notebooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    kernel: Mapped[str] = mapped_column(String(64), default="python")  # python | sql | bash
    atlas_model: Mapped[str] = mapped_column(String(64), default="atlas-code")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )

    cells: Mapped[list["NotebookCell"]] = relationship(
        back_populates="notebook", order_by="NotebookCell.sequence", cascade="all, delete-orphan"
    )


class NotebookCell(Base):
    """A single cell in a notebook: code, markdown, or AI prompt."""

    __tablename__ = "notebook_cells"
    __table_args__ = (
        CheckConstraint("cell_type IN ('code','markdown','ai')", name="chk_cell_type"),
        CheckConstraint(
            "execution_state IN ('idle','running','completed','error')",
            name="chk_cell_exec_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    cell_type: Mapped[str] = mapped_column(String(16), default="code")
    source: Mapped[str] = mapped_column(Text, default="")  # user code or prompt
    output: Mapped[str | None] = mapped_column(Text, nullable=True)  # execution result
    ai_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_state: Mapped[str] = mapped_column(String(16), default="idle")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notebook: Mapped[Notebook] = relationship(back_populates="cells")


# P4 Indexes
Index("ix_computer_sessions_workspace", ComputerSession.workspace_id)
Index("ix_computer_sessions_agent", ComputerSession.agent_id)
Index("ix_notebooks_workspace", Notebook.workspace_id)
Index("ix_notebook_cells_notebook", NotebookCell.notebook_id)


# ---------------------------------------------------------------------------
# Phase 4: Governance -- model registry, approvals, audit, budgets
# ---------------------------------------------------------------------------


class ModelVersion(Base):
    """Tracks trained model versions through their lifecycle.

    draft -> staging -> active -> retired
    """

    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','staging','active','retired')",
            name="chk_model_version_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_model: Mapped[str] = mapped_column(String(256), nullable=False)
    adapter_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_runs.id", ondelete="SET NULL"), nullable=True
    )
    eval_scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "v1.2"
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelDeployment(Base):
    """Tracks where a model version is deployed."""

    __tablename__ = "model_deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','deploying','active','failed','removed')",
            name="chk_deployment_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    target_server: Mapped[str] = mapped_column(
        String(256), nullable=False
    )  # e.g. "http://10.0.0.9:11434"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


class ApprovalRequest(Base):
    """Human approval gate for model promotions and other governed actions.

    status: pending -> approved | rejected
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="chk_approval_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "model_version", "agent", "handoff"
    entity_id: Mapped[str] = mapped_column(
        String(256), nullable=False
    )  # UUID or identifier of the entity
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "promote", "graduate", "activate"
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    requested_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # context (eval scores, etc.)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Immutable audit log for admin actions."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "create", "update", "delete", "execute", "approve", "reject"
    entity_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "agent", "application", "workspace", "training_run", "model_version"
    entity_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


class WorkspaceBudget(Base):
    """Per-workspace monthly cost budget with enforcement."""

    __tablename__ = "workspace_budgets"
    __table_args__ = (UniqueConstraint("workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    monthly_limit_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=100.0)
    current_month_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    alert_threshold_pct: Mapped[float] = mapped_column(Float, default=0.8)  # alert at 80%
    budget_month: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-04" format
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )


# Phase 4 Governance Indexes
Index("ix_model_versions_task", ModelVersion.task_type)
Index("ix_model_versions_status", ModelVersion.status)
Index("ix_model_deployments_version", ModelDeployment.model_version_id)
Index("ix_approval_requests_status", ApprovalRequest.status)
Index("ix_approval_requests_entity", ApprovalRequest.entity_type, ApprovalRequest.entity_id)
Index("ix_audit_events_workspace", AuditEvent.workspace_id)
Index("ix_audit_events_created", AuditEvent.created_at)
Index("ix_audit_events_entity", AuditEvent.entity_type)
Index("ix_workspace_budgets_workspace", WorkspaceBudget.workspace_id)


# ---------------------------------------------------------------------------
# Web Search Layer — P5 schema
# ---------------------------------------------------------------------------


class WebSearchRun(Base):
    """One web search query execution — the top-level record for a search event.

    Stores the PII-masked query, which provider answered it, result count,
    latency, and whether it was blocked before sending to the search API.
    """

    __tablename__ = "web_search_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    # PII-safe query (masker applied before storage)
    query_masked: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 prefix of the original query for deduplication analytics (not reversible)
    query_raw_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0.0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # "high_intent", etc.
    atlas_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    results: Mapped[list["WebSearchResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    citations: Mapped[list["WebCitation"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class WebSearchResult(Base):
    """Individual search result returned by a search provider.

    One WebSearchRun → N WebSearchResults (typically 3-10).
    """

    __tablename__ = "web_search_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("web_search_runs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO date
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)  # domain
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    was_extracted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    run: Mapped[WebSearchRun] = relationship(back_populates="results")
    extracted_page: Mapped["WebExtractedPage | None"] = relationship(
        back_populates="result", uselist=False
    )


class WebExtractedPage(Base):
    """Cleaned page content fetched from a search result URL.

    Stored for analytics and as RAG training data.
    """

    __tablename__ = "web_extracted_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("web_search_results.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # Truncated page content (~500 words) used as LLM grounding context
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extraction_ok: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    result: Mapped[WebSearchResult] = relationship(back_populates="extracted_page")


class WebCitation(Base):
    """A verifiable source linked to an LLM response.

    Created after the LLM responds so we can detect which [N] markers were used.
    Tied back to the WebSearchRun for analytics.
    """

    __tablename__ = "web_citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("web_search_runs.id", ondelete="CASCADE"), nullable=False
    )
    routing_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routing_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    claim_supported: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run: Mapped[WebSearchRun] = relationship(back_populates="citations")


# Web Search Indexes
Index("ix_web_search_runs_workspace", WebSearchRun.workspace_id)
Index("ix_web_search_runs_created", WebSearchRun.created_at)
Index("ix_web_search_runs_provider", WebSearchRun.provider)
Index("ix_web_search_results_run", WebSearchResult.run_id)
Index("ix_web_extracted_pages_result", WebExtractedPage.result_id)
Index("ix_web_citations_run", WebCitation.run_id)
Index("ix_web_citations_routing", WebCitation.routing_decision_id)


class UploadedFile(Base):
    """Stores metadata for files uploaded via POST /v1/files."""

    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # "file-<hex>"
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # assistants|batch|fine-tune|vision
    bytes_: Mapped[int] = mapped_column("bytes", Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)  # absolute path on disk
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)


Index("ix_uploaded_files_purpose", UploadedFile.purpose)
Index("ix_uploaded_files_workspace", UploadedFile.workspace_id)
Index("ix_uploaded_files_created", UploadedFile.created_at)


# Vector store (pgvector-backed semantic search)
class VectorStore(Base):
    """A named collection of embedded document chunks."""

    __tablename__ = "vector_stores"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # "vs-<hex>"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )

    chunks: Mapped[list["VectorChunk"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class VectorChunk(Base):
    """A single embedded chunk stored in a vector store."""

    __tablename__ = "vector_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("vector_stores.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # links to uploaded_files
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(
        Vector(None), nullable=False
    )  # dim varies: 768=nomic, 3072=gemini
    model: Mapped[str] = mapped_column(String(128), nullable=False)  # embedding model used
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)

    store: Mapped[VectorStore] = relationship(back_populates="chunks")


Index("ix_vector_chunks_store", VectorChunk.store_id)
Index("ix_vector_chunks_file", VectorChunk.file_id)
Index("ix_vector_stores_workspace", VectorStore.workspace_id)


class Batch(Base):
    """Async batch processing job — OpenAI Batch API compatible."""

    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # "batch-<hex>"
    input_file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    output_file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint: Mapped[str] = mapped_column(
        String(128), nullable=False, default="/v1/chat/completions"
    )
    completion_window: Mapped[str] = mapped_column(String(16), nullable=False, default="24h")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="validating")
    # validating | in_progress | finalizing | completed | failed | cancelled | expired
    total_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalizing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index("ix_batches_status", Batch.status)
Index("ix_batches_created", Batch.created_at)
Index("ix_batches_workspace", Batch.workspace_id)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt versioning (Phase 2.1)
# ─────────────────────────────────────────────────────────────────────────────


class PromptVersion(Base):
    """A versioned, named prompt template.

    Lets prompts (system-prompt suffixes, the self-critique template, etc.) live
    in the DB instead of hardcoded in source / static YAML, so they can be
    edited and A/B-tested without a redeploy. The loader falls back to code
    defaults when no active version exists, so behavior is preserved.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_name_version"),
        Index("ix_prompt_name_active", "name", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # logical key, e.g. "self_critique"
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional model scoping (e.g. an atlas-code-specific suffix). NULL = global.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_ist)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_ist, onupdate=_now_ist
    )


Index("ix_prompt_versions_name", PromptVersion.name)
