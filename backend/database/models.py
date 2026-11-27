"""SQLAlchemy ORM models. Postgres is the system of record; SQLite is used for offline tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONType, list[Any]: JSONType}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))  # requester | reviewer
    department: Mapped[str] = mapped_column(String(64), default="unknown")
    active: Mapped[bool] = mapped_column(default=True)
    password_hash: Mapped[str | None] = mapped_column(String(200))


class Request(TimestampMixin, Base):
    __tablename__ = "requests"
    id: Mapped[uuid.UUID] = _uuid_pk()
    description: Mapped[str] = mapped_column(Text)
    requester_id: Mapped[str] = mapped_column(String(64), index=True)
    department: Mapped[str] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[str] = mapped_column(String(16))
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(32), index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    dedupe_hash: Mapped[str] = mapped_column(String(64), index=True)

    runs: Mapped[list[WorkflowRun]] = relationship(back_populates="request")


class Workflow(TimestampMixin, Base):
    """A workflow definition (e.g. ``partner_onboarding``)."""

    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    stages: Mapped[list[Any]] = mapped_column(default=list)


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    framework: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    refinements: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    # Lease for crash recovery: a worker owns the run until lease_expires_at.
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))

    request: Mapped[Request] = relationship(back_populates="runs")


class AgentExecution(TimestampMixin, Base):
    __tablename__ = "agent_executions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    agent: Mapped[str] = mapped_column(String(32))
    framework: Mapped[str] = mapped_column(String(32))
    framework_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_executions.id"))
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str | None] = mapped_column(String(32))
    agent: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # ok | error | denied
    args_redacted: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceRecord(Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("run_id", "ref"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    ref: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    collected_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProposedActionRecord(TimestampMixin, Base):
    __tablename__ = "proposed_actions"
    __table_args__ = (UniqueConstraint("run_id", "key"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    action_type: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence_refs: Mapped[list[Any]] = mapped_column(default=list)
    depends_on: Mapped[list[Any]] = mapped_column(default=list)
    risk_class: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    validation_notes: Mapped[list[Any]] = mapped_column(default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)


class Approval(TimestampMixin, Base):
    """Approval records are append-only once decided (enforced in the approvals service)."""

    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = _uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposed_actions.id"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    action_hash: Mapped[str] = mapped_column(String(64))
    action_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    risk_class: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)


class ActionExecution(Base):
    __tablename__ = "action_executions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposed_actions.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # succeeded | failed | retryable_error
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_redacted: Mapped[dict[str, Any]] = mapped_column(default=dict)
    response_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyKey(Base):
    """Guarantees a side effect is attempted under one key at most once to completion."""

    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposed_actions.id"))
    status: Mapped[str] = mapped_column(String(16))  # in_progress | completed
    external_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    aggregate_type: Mapped[str] = mapped_column(String(32))
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Integration(TimestampMixin, Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str] = mapped_column(String(256))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    framework: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(32))
    dataset_version: Mapped[str] = mapped_column(String(64))
    scorer_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[uuid.UUID] = _uuid_pk()
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(64))
    trial: Mapped[int] = mapped_column(Integer)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    success: Mapped[bool] = mapped_column(default=False)
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    failure_tags: Mapped[list[Any]] = mapped_column(default=list)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
