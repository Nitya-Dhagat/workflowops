"""Framework-agnostic agent adapter contract.

Framework-specific objects (ADK agents, LangGraph graphs, ...) never leave
``backend/frameworks/<name>/``. Everything crossing this boundary is a Pydantic model.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from backend.domain.schemas import Evidence


class TaskType(StrEnum):
    CLASSIFY = "classify"
    RESEARCH = "research"
    POLICY = "policy"
    DATA = "data"
    PLAN = "plan"
    VALIDATE = "validate"  # advisory only
    REPORT = "report"


# Stage-output schema each task must satisfy (validated by the engine).
AGENT_FOR_TASK: dict[TaskType, str] = {
    TaskType.CLASSIFY: "intake",
    TaskType.RESEARCH: "research",
    TaskType.POLICY: "policy",
    TaskType.DATA: "data",
    TaskType.PLAN: "planning",
    TaskType.VALIDATE: "validation",
    TaskType.REPORT: "reporting",
}


class ModelConfig(BaseModel):
    provider: str = "none"
    model: str = "none"
    temperature: float = 0.0
    max_output_tokens: int = 2048


class AgentTaskRequest(BaseModel):
    task_type: TaskType
    run_id: uuid.UUID
    execution_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)  # read-only
    tool_manifest: list[str] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_config_: ModelConfig = Field(default_factory=ModelConfig, alias="model_config")
    prompt_version: str = "v1"
    deadline_s: float = 30.0
    idempotency_key: str = ""
    trace_context: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd_estimate: float = 0.0


class ToolCallSummary(BaseModel):
    tool: str
    status: Literal["ok", "error", "denied"]
    latency_ms: float = 0.0


class PlatformEventType(StrEnum):
    AGENT_STARTED = "agent_started"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_TRANSITION = "state_transition"
    ERROR = "error"
    AGENT_FINISHED = "agent_finished"


class PlatformEvent(BaseModel):
    type: PlatformEventType
    agent: str | None = None
    stage: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd_estimate: float = 0.0


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class AgentTaskResult(BaseModel):
    execution_id: uuid.UUID
    status: ExecutionStatus
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    events: list[PlatformEvent] = Field(default_factory=list)
    error: str | None = None


class ResumeSignal(BaseModel):
    kind: Literal["approval", "requester_info", "manual"]
    payload: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class AgentFrameworkAdapter(Protocol):
    name: str
    version: str

    async def execute(self, request: AgentTaskRequest) -> AgentTaskResult: ...
    def stream(self, request: AgentTaskRequest) -> AsyncIterator[PlatformEvent]: ...
    async def get_status(self, execution_id: str) -> ExecutionStatus: ...
    async def cancel(self, execution_id: str) -> None: ...
    async def resume(self, execution_id: str, signal: ResumeSignal) -> AgentTaskResult: ...
