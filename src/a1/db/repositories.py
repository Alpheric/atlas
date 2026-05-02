import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from a1.db.models import (
    Conversation,
    DualExecutionRecord,
    Message,
    ModelPerformance,
    QualitySignal,
    RoutingDecision,
    TaskTypeReadiness,
    TrainingRun,
)


class ConversationRepo:
    def __init__(self, session: AsyncSession, workspace_id: str | None = None):
        self.session = session
        self.workspace_id = workspace_id

    async def create(
        self,
        source: str,
        user_id: str | None = None,
        external_id: str | None = None,
        metadata: dict | None = None,
    ) -> Conversation:
        conv = Conversation(
            source=source,
            user_id=user_id,
            external_id=external_id,
            metadata_=metadata or {},
            workspace_id=uuid.UUID(self.workspace_id) if self.workspace_id else None,
        )
        self.session.add(conv)
        await self.session.flush()
        return conv

    def _apply_workspace_filter(self, stmt):
        """Apply workspace scoping if a workspace_id was provided."""
        if self.workspace_id:
            stmt = stmt.where(Conversation.workspace_id == uuid.UUID(self.workspace_id))
        return stmt

    async def get(self, conv_id: uuid.UUID) -> Conversation | None:
        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.messages).selectinload(Message.routing_decision))
            .options(selectinload(Conversation.messages).selectinload(Message.quality_signals))
            .where(Conversation.id == conv_id)
        )
        stmt = self._apply_workspace_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        source: str | None = None,
    ) -> list[Conversation]:
        from datetime import datetime as _dt

        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.messages).selectinload(Message.routing_decision))
            .order_by(Conversation.created_at.desc())
        )
        stmt = self._apply_workspace_filter(stmt)
        if search:
            stmt = stmt.where(Conversation.user_id.ilike(f"%{search}%"))
        if source:
            stmt = stmt.where(Conversation.source == source)
        if date_from:
            stmt = stmt.where(Conversation.created_at >= _dt.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(Conversation.created_at <= _dt.fromisoformat(date_to))
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        source: str | None = None,
    ) -> int:
        from datetime import datetime as _dt

        stmt = select(func.count(Conversation.id))
        stmt = self._apply_workspace_filter(stmt)
        if search:
            stmt = stmt.where(Conversation.user_id.ilike(f"%{search}%"))
        if source:
            stmt = stmt.where(Conversation.source == source)
        if date_from:
            stmt = stmt.where(Conversation.created_at >= _dt.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(Conversation.created_at <= _dt.fromisoformat(date_to))
        result = await self.session.execute(stmt)
        return result.scalar_one()


class MessageRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        sequence: int,
        tool_calls: dict | None = None,
        token_count: int | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence=sequence,
            tool_calls=tool_calls,
            token_count=token_count,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg


class RoutingRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        message_id: uuid.UUID,
        provider: str,
        model: str,
        strategy: str,
        task_type: str | None,
        confidence: float | None,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        error: str | None = None,
        fallback_from: uuid.UUID | None = None,
        is_local: bool = False,
        api_key_hash: str | None = None,
        cache_hit: bool = False,
        account_id: uuid.UUID | None = None,
        self_healed: bool = False,
        heal_score_before: float | None = None,
    ) -> RoutingDecision:
        decision = RoutingDecision(
            message_id=message_id,
            provider=provider,
            model=model,
            strategy=strategy,
            task_type=task_type,
            confidence=confidence,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            error=error,
            fallback_from=fallback_from,
            is_local=is_local,
            api_key_hash=api_key_hash,
            cache_hit=cache_hit,
            account_id=account_id,
            self_healed=self_healed,
            heal_score_before=heal_score_before,
        )
        self.session.add(decision)
        await self.session.flush()
        return decision

    async def list_recent(
        self,
        limit: int = 100,
        date_from: str | None = None,
        date_to: str | None = None,
        task_type: str | None = None,
    ) -> list[RoutingDecision]:
        stmt = select(RoutingDecision)
        if date_from:
            dt_from = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            stmt = stmt.where(RoutingDecision.created_at >= dt_from)
        if date_to:
            dt_to = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
            stmt = stmt.where(RoutingDecision.created_at <= dt_to)
        if task_type:
            stmt = stmt.where(RoutingDecision.task_type == task_type)
        stmt = stmt.order_by(RoutingDecision.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_performance(self, task_type: str | None = None) -> list[ModelPerformance]:
        stmt = select(ModelPerformance)
        if task_type:
            stmt = stmt.where(ModelPerformance.task_type == task_type)
        stmt = stmt.order_by(ModelPerformance.avg_quality.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class QualityRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_signal(
        self, message_id: uuid.UUID, signal_type: str, value: float, evaluator: str | None = None
    ) -> QualitySignal:
        signal = QualitySignal(
            message_id=message_id, signal_type=signal_type, value=value, evaluator=evaluator
        )
        self.session.add(signal)
        await self.session.flush()
        return signal


class TrainingRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_run(self, base_model: str, dataset_size: int, config: dict) -> TrainingRun:
        run = TrainingRun(base_model=base_model, dataset_size=dataset_size, config=config)
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run(self, run_id: uuid.UUID) -> TrainingRun | None:
        result = await self.session.execute(select(TrainingRun).where(TrainingRun.id == run_id))
        return result.scalar_one_or_none()

    async def list_runs(self, limit: int = 50) -> list[TrainingRun]:
        stmt = select(TrainingRun).order_by(TrainingRun.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(self, run_id: uuid.UUID, status: str, **kwargs) -> None:
        run = await self.get_run(run_id)
        if run:
            run.status = status
            for k, v in kwargs.items():
                setattr(run, k, v)
            await self.session.flush()


class DualExecutionRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> DualExecutionRecord:
        record = DualExecutionRecord(**kwargs)
        self.session.add(record)
        await self.session.flush()
        return record

    async def count_by_task_type(self, task_type: str, min_quality: float = 0.0) -> int:
        stmt = select(func.count(DualExecutionRecord.id)).where(
            DualExecutionRecord.task_type == task_type,
            DualExecutionRecord.similarity_score >= min_quality,
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_recent(
        self, task_type: str | None = None, limit: int = 50
    ) -> list[DualExecutionRecord]:
        stmt = (
            select(DualExecutionRecord).order_by(DualExecutionRecord.created_at.desc()).limit(limit)
        )
        if task_type:
            stmt = stmt.where(DualExecutionRecord.task_type == task_type)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_unused_for_training(
        self, task_type: str, min_quality: float = 0.0, limit: int = 10000
    ) -> list[DualExecutionRecord]:
        """Return unused training records, optionally filtered by quality_score.

        Args:
            task_type:   Task type to fetch records for.
            min_quality: Minimum quality_score to include (0.0 = no filter).
                         Set to ``settings.distillation_quality_threshold`` to enforce
                         the quality gate on training data.
            limit:       Maximum records to return.
        """
        stmt = select(DualExecutionRecord).where(
            DualExecutionRecord.task_type == task_type,
            DualExecutionRecord.similarity_score.isnot(None),
            DualExecutionRecord.used_for_training.is_(False),
        )
        if min_quality > 0.0:
            stmt = stmt.where(
                (DualExecutionRecord.quality_score >= min_quality)
                | DualExecutionRecord.quality_score.is_(None)
            )
        stmt = stmt.order_by(DualExecutionRecord.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class TaskTypeReadinessRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, task_type: str) -> TaskTypeReadiness:
        stmt = select(TaskTypeReadiness).where(TaskTypeReadiness.task_type == task_type)
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            record = TaskTypeReadiness(task_type=task_type)
            self.session.add(record)
            await self.session.flush()
        return record

    async def update_handoff(
        self, task_type: str, handoff_pct: float, best_model: str | None = None
    ) -> None:
        record = await self.get_or_create(task_type)
        record.local_handoff_pct = handoff_pct
        if best_model:
            record.best_local_model = best_model
        await self.session.flush()

    async def increment_sample_count(self, task_type: str) -> int:
        record = await self.get_or_create(task_type)
        record.claude_sample_count += 1
        await self.session.flush()
        return record.claude_sample_count

    async def list_all(self) -> list[TaskTypeReadiness]:
        result = await self.session.execute(
            select(TaskTypeReadiness).order_by(TaskTypeReadiness.task_type)
        )
        return list(result.scalars().all())
