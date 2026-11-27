"""Platform-owned workflow engine for ``partner_onboarding`` (Mode A: stage executor).

The engine is a durable state machine driven by the request status persisted in Postgres.
``advance(run_id)`` runs stages until the workflow pauses (approval / requester info) or
terminates. Each stage commits its output together with its status transition, so a crashed
worker can call ``advance`` again and continue from the last committed stage.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import Settings
from backend.core.logging import get_logger
from backend.database.models import (
    AgentEvent,
    AgentExecution,
    Approval,
    EvidenceRecord,
    OutboxEvent,
    ProposedActionRecord,
    Request,
    ToolInvocation,
    WorkflowRun,
    utcnow,
)
from backend.domain.enums import (
    ActionStatus,
    ApprovalStatus,
    RequestStatus,
    RunStatus,
)
from backend.domain.schemas import (
    ActionPlan,
    Classification,
    Evidence,
    InvestigationReport,
    Report,
)
from backend.domain.state_machine import assert_transition, is_terminal
from backend.frameworks.base import (
    AGENT_FOR_TASK,
    AgentFrameworkAdapter,
    AgentTaskRequest,
    AgentTaskResult,
    ExecutionStatus,
    TaskType,
)
from backend.integrations.base import ConnectorError
from backend.integrations.connectors import ConnectorSet
from backend.orchestration.bindings import locate
from backend.orchestration.executor import ActionExecutor, RetryPolicy, Sleep
from backend.policy.catalog import action_hash, idempotency_key
from backend.policy.engine import PolicyConfig, validate_plan
from backend.policy.facts import PartnerFacts
from backend.tools.gateway import AGENT_ALLOWLIST, ToolGateway

log = get_logger(__name__)
S = RequestStatus
WORKFLOW_ID = "partner_onboarding"
LEASE = timedelta(minutes=5)
MAX_REFINEMENTS = 2
PLANNER_ERROR_CHECKS = {"schema", "allowlist", "evidence", "duplicate"}
PROMPT_VERSIONS = {a: "v1" for a in AGENT_FOR_TASK.values()}

AdapterFactory = Callable[[ToolGateway], AgentFrameworkAdapter]
STAGE_SCHEMAS: dict[TaskType, type[BaseModel]] = {
    TaskType.CLASSIFY: Classification,
    TaskType.RESEARCH: InvestigationReport,
    TaskType.POLICY: InvestigationReport,
    TaskType.DATA: InvestigationReport,
    TaskType.PLAN: ActionPlan,
    TaskType.REPORT: Report,
}


class StageError(Exception):
    pass


class WorkflowEngine:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        connectors: ConnectorSet,
        adapters: dict[str, AdapterFactory],
        settings: Settings,
        *,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        worker_id: str | None = None,
        claim_wait_s: float = 10.0,
    ) -> None:
        self.sm = sessionmaker
        self.connectors = connectors
        self.adapters = adapters
        self.settings = settings
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.claim_wait_s = claim_wait_s
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        # Test hook: called after each committed stage with the new status.
        self.after_stage: Callable[[RequestStatus], Awaitable[None]] | None = None

    # ================================================================== public API
    async def create_run(
        self, request_id: uuid.UUID, framework: str = "rules", mode: str = "stage_executor"
    ) -> uuid.UUID:
        if framework not in self.adapters:
            raise ValueError(f"unknown framework `{framework}`")
        async with self.sm() as s:
            req = await s.get(Request, request_id)
            if req is None:
                raise LookupError("request not found")
            run = WorkflowRun(
                request_id=request_id,
                workflow_id=WORKFLOW_ID,
                framework=framework,
                mode=mode,
                status=RunStatus.PENDING,
                state={},
            )
            s.add(run)
            await s.commit()
            return run.id

    async def advance(self, run_id: uuid.UUID) -> RequestStatus:
        """Drive a run until it pauses or ends. Safe to call from many workers at once.

        An in-process lock serializes callers inside this worker; a lease claimed with a
        conditional UPDATE in the database serializes workers. A worker that cannot claim the
        lease waits briefly (the holder may be about to pause), then leaves the run to it.
        """
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            deadline = time.monotonic() + self.claim_wait_s
            while not await self._claim(run_id):
                if time.monotonic() >= deadline:
                    log.info("run_claimed_elsewhere", run=str(run_id), worker=self.worker_id)
                    async with self.sm() as s:
                        _, req = await self._load(s, run_id)
                        return S(req.status)
                await asyncio.sleep(0.25)
            return await self._advance(run_id)

    async def _claim(self, run_id: uuid.UUID) -> bool:
        """Atomically take (or renew) the run's lease. True only for the lease holder."""
        now = utcnow()
        async with self.sm() as s:
            res = await s.execute(
                update(WorkflowRun)
                .where(
                    WorkflowRun.id == run_id,
                    or_(
                        WorkflowRun.lease_owner.is_(None),
                        WorkflowRun.lease_owner == self.worker_id,
                        WorkflowRun.lease_expires_at < now,
                    ),
                )
                .values(
                    lease_owner=self.worker_id,
                    lease_expires_at=now + LEASE,
                    status=RunStatus.RUNNING,
                )
                .execution_options(synchronize_session=False)
            )
            await s.commit()
            return bool(getattr(res, "rowcount", 0) == 1)

    async def cancel(self, run_id: uuid.UUID, reason: str = "cancelled by user") -> None:
        async with self.sm() as s:
            run, req = await self._load(s, run_id)
            if not is_terminal(S(req.status)):
                await self._transition(s, req, run, S.CANCELLED, reason)
                run.status = RunStatus.CANCELLED
                await s.commit()

    async def recover(self) -> list[uuid.UUID]:
        """Resume runs whose worker died (running with an expired lease)."""
        now = utcnow()
        async with self.sm() as s:
            rows = await s.scalars(
                select(WorkflowRun).where(WorkflowRun.status == RunStatus.RUNNING)
            )
            stale = [
                r.id for r in rows if r.lease_expires_at is None or _aware(r.lease_expires_at) < now
            ]
        for rid in stale:
            await self.advance(rid)
        return stale

    # ================================================================== driver
    async def _advance(self, run_id: uuid.UUID) -> RequestStatus:
        gateway = ToolGateway(self.connectors)
        adapter = self.adapters[await self._framework(run_id)](gateway)

        while True:
            # Renew the lease before every stage; if another worker took over after our lease
            # expired, stop instead of racing it.
            if not await self._claim(run_id):
                async with self.sm() as s:
                    _, req = await self._load(s, run_id)
                    return S(req.status)
            async with self.sm() as s:
                run, req = await self._load(s, run_id)
                status = S(req.status)
                if is_terminal(status) or status is S.AWAITING_REQUESTER_INFO:
                    run.status = _run_status(status)
                    run.lease_owner = None
                    await s.commit()
                    return status
                try:
                    progressed = await self._step(s, run, req, status, adapter, gateway)
                except (StageError, ConnectorError, ValidationError) as e:
                    await s.rollback()
                    run, req = await self._load(s, run_id)
                    log.warning("stage_failed", run=str(run_id), status=status, error=str(e))
                    await self._flush_tools(s, gateway)
                    await self._event(s, run, "error", {"error": str(e)}, stage=status.value)
                    await self._finish(s, run, req, adapter, S.FAILED, f"error: {e}")
                    await s.commit()
                    continue
                await self._flush_tools(s, gateway)
                await s.commit()
                if not progressed:  # paused
                    run.status = RunStatus.PAUSED
                    run.lease_owner = None
                    await s.commit()
                    return S(req.status)
            if self.after_stage:
                await self.after_stage(S(req.status))

    async def _step(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        req: Request,
        status: S,
        adapter: AgentFrameworkAdapter,
        gw: ToolGateway,
    ) -> bool:
        match status:
            case S.RECEIVED:
                await self._transition(s, req, run, S.CLASSIFYING)
            case S.CLASSIFYING:
                await self._stage_classify(s, run, req, adapter)
            case S.INVESTIGATING:
                await self._stage_investigate(s, run, req, adapter, gw)
            case S.PLANNING:
                await self._stage_plan(s, run, req, adapter)
            case S.VALIDATING:
                await self._stage_validate(s, run, req, adapter)
            case S.AWAITING_APPROVAL:
                return await self._stage_approval(s, run, req, adapter)
            case S.EXECUTING:
                await self._stage_execute(s, run, req, gw)
            case S.VERIFYING:
                await self._stage_verify(s, run, req, adapter, gw)
            case _:
                raise StageError(f"no handler for status {status}")
        return True

    # ================================================================== stages
    async def _stage_classify(
        self, s: AsyncSession, run: WorkflowRun, req: Request, adapter: AgentFrameworkAdapter
    ) -> None:
        out = await self._agent(
            s,
            run,
            adapter,
            TaskType.CLASSIFY,
            "intake",
            {
                "description": req.description,
                "attachments": req.attachments,  # untrusted data
                "requester_id": req.requester_id,
                "priority": req.priority,
            },
        )
        cls = Classification.model_validate(out)
        _set(run, "classification", cls.model_dump())
        if not cls.in_scope:
            await self._finish(
                s, run, req, adapter, S.FAILED, f"rejected: out of scope ({cls.rationale})"
            )
        elif cls.missing_info or not cls.partner_id:
            await self._finish(
                s,
                run,
                req,
                adapter,
                S.AWAITING_REQUESTER_INFO,
                f"missing information: {', '.join(cls.missing_info)}",
            )
        else:
            await self._transition(s, req, run, S.INVESTIGATING)

    async def _stage_investigate(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        req: Request,
        adapter: AgentFrameworkAdapter,
        gw: ToolGateway,
    ) -> None:
        cls = Classification.model_validate(run.state["classification"])
        inp = {
            "partner_id": cls.partner_id,
            "partner_name": cls.partner_name,
            "request": req.description,
        }
        sem = asyncio.Semaphore(self.settings.investigation_concurrency)
        tasks = [TaskType.RESEARCH, TaskType.POLICY, TaskType.DATA]

        async def one(t: TaskType) -> tuple[AgentTaskRequest, AgentTaskResult, float]:
            areq = self._task_request(run, t, inp)
            async with sem:
                t0 = time.perf_counter()
                try:
                    async with asyncio.timeout(self.settings.investigation_timeout_s):
                        res = await adapter.execute(areq)
                except TimeoutError:
                    res = AgentTaskResult(
                        execution_id=areq.execution_id,
                        status=ExecutionStatus.FAILED,
                        error="timeout",
                    )
                return areq, res, (time.perf_counter() - t0) * 1000

        results = await asyncio.gather(*(one(t) for t in tasks))
        reports: dict[str, Any] = {}
        for areq, res, ms in results:
            out = await self._persist_agent(s, run, adapter, "investigation", areq, res, ms)
            reports[AGENT_FOR_TASK[areq.task_type]] = out
        _set(run, "reports", reports)

        facts = await self._gather_facts(gw, cls.partner_id, req.requester_id)
        _set(run, "facts", facts.model_dump(mode="json"))
        if facts.partner is None:
            await self._finish(s, run, req, adapter, S.FAILED, "rejected: partner not found")
        elif facts.restricted():
            await self._finish(
                s,
                run,
                req,
                adapter,
                S.FAILED,
                "rejected: partner in restricted region (POL-ONB-007)",
            )
        elif not facts.onboardable_status():
            await self._finish(
                s,
                run,
                req,
                adapter,
                S.FAILED,
                f"rejected: partner account status is "
                f"{facts.partner['account_status']} (POL-ONB-001)",
            )
        else:
            await self._transition(s, req, run, S.PLANNING)

    async def _stage_plan(
        self, s: AsyncSession, run: WorkflowRun, req: Request, adapter: AgentFrameworkAdapter
    ) -> None:
        out = await self._agent(
            s,
            run,
            adapter,
            TaskType.PLAN,
            "planning",
            {
                "classification": run.state["classification"],
                "reports": run.state["reports"],
                "requester_id": req.requester_id,
                "feedback": run.state.get("feedback", []),
            },
        )
        _set(run, "plan", ActionPlan.model_validate(out).model_dump())
        await self._transition(s, req, run, S.VALIDATING)

    async def _stage_validate(
        self, s: AsyncSession, run: WorkflowRun, req: Request, adapter: AgentFrameworkAdapter
    ) -> None:
        plan = ActionPlan.model_validate(run.state["plan"])
        facts = PartnerFacts.model_validate(run.state["facts"])
        known = set(
            await s.scalars(select(EvidenceRecord.ref).where(EvidenceRecord.run_id == run.id))
        )
        cfg = PolicyConfig(
            medium_risk_requires_approval=self.settings.medium_risk_requires_approval
        )
        report = validate_plan(plan, facts, known, cfg)
        advisory = await self._agent(
            s,
            run,
            adapter,
            TaskType.VALIDATE,
            "validation",
            {"plan": plan.model_dump(), "deterministic": report.model_dump(mode="json")},
        )
        _set(run, "validation", report.model_dump(mode="json"))
        _set(run, "advisory_validation", advisory)

        planner_errors = [i for i in report.issues if i.check in PLANNER_ERROR_CHECKS]
        if planner_errors and run.refinements < MAX_REFINEMENTS:
            run.refinements += 1
            _set(run, "feedback", [i.model_dump() for i in planner_errors])
            await self._transition(s, req, run, S.PLANNING, "refining plan")
            return

        # Persist proposals + approvals atomically with the transition.
        await s.execute(delete(Approval).where(Approval.run_id == run.id))
        await s.execute(delete(ProposedActionRecord).where(ProposedActionRecord.run_id == run.id))
        notes: dict[str, list[str]] = {}
        for i in report.issues:
            notes.setdefault(i.action_key or "", []).append(f"[{i.check}] {i.message}")
        ttl = timedelta(hours=self.settings.approval_ttl_hours)
        for a in plan.actions:
            accepted = a.key in report.accepted
            risk = report.effective_risk.get(a.key, a.risk_class)
            if not accepted:
                status = ActionStatus.REJECTED_BY_VALIDATION
            elif a.key in report.needs_approval:
                status = ActionStatus.PENDING_APPROVAL
            else:
                status = ActionStatus.APPROVED
                notes.setdefault(a.key, []).append(f"auto-approved by policy (risk={risk})")
            h = action_hash(a.action_type, a.params)
            rec = ProposedActionRecord(
                run_id=run.id,
                key=a.key,
                action_type=a.action_type,
                params=a.params,
                evidence_refs=a.evidence_refs,
                depends_on=a.depends_on,
                risk_class=risk,
                status=status,
                payload_hash=h,
                validation_notes=notes.get(a.key, []),
                idempotency_key=idempotency_key(str(req.id), a.action_type, a.params),
            )
            s.add(rec)
            await s.flush()
            if status is ActionStatus.PENDING_APPROVAL:
                s.add(
                    Approval(
                        action_id=rec.id,
                        run_id=run.id,
                        status=ApprovalStatus.PENDING,
                        action_hash=h,
                        risk_class=risk,
                        expires_at=utcnow() + ttl,
                        action_snapshot={
                            "action_type": a.action_type,
                            "params": a.params,
                            "evidence_refs": a.evidence_refs,
                            "notes": notes.get(a.key, []),
                        },
                    )
                )
        if report.needs_approval:
            await self._transition(s, req, run, S.AWAITING_APPROVAL)
        elif report.accepted:
            await self._transition(s, req, run, S.EXECUTING)
        else:
            await self._finish(
                s,
                run,
                req,
                adapter,
                S.COMPLETED_WITH_WARNINGS,
                "no executable actions: " + (plan.rationale or "plan empty"),
            )

    async def _stage_approval(
        self, s: AsyncSession, run: WorkflowRun, req: Request, adapter: AgentFrameworkAdapter
    ) -> bool:
        approvals = list(await s.scalars(select(Approval).where(Approval.run_id == run.id)))
        now = utcnow()
        for ap in approvals:
            if ap.status == ApprovalStatus.PENDING and _aware(ap.expires_at) <= now:
                ap.status = ApprovalStatus.EXPIRED
                ap.decided_at = now
                act = await s.get(ProposedActionRecord, ap.action_id)
                assert act is not None
                act.status = ActionStatus.EXPIRED
        if any(ap.status == ApprovalStatus.PENDING for ap in approvals):
            return False  # stay paused, durably
        actions = await self._actions(s, run.id)
        if any(a.status == ActionStatus.APPROVED for a in actions):
            await self._transition(s, req, run, S.EXECUTING)
        else:
            status, reason = _final_status(actions)
            await self._finish(s, run, req, adapter, status, reason)
        return True

    async def _stage_execute(
        self, s: AsyncSession, run: WorkflowRun, req: Request, gw: ToolGateway
    ) -> None:
        executor = ActionExecutor(
            gw,
            RetryPolicy(
                self.settings.max_action_attempts,
                self.settings.retry_base_delay_s,
                self.settings.retry_max_delay_s,
            ),
            self.sleep,
            self.rng,
        )
        actions = _topo(await self._actions(s, run.id))
        by_key = {a.key: a for a in actions}
        for a in actions:
            if a.status not in {ActionStatus.APPROVED, ActionStatus.EXECUTING}:
                continue
            blocked = [
                d
                for d in a.depends_on
                if by_key[d].status not in {ActionStatus.SUCCEEDED, ActionStatus.VERIFIED}
            ]
            if blocked:
                a.status = ActionStatus.SKIPPED
                a.result = {"reason": f"dependency not executed: {blocked}"}
                await s.commit()
                continue
            await self._event(
                s,
                run,
                "tool_call",
                {"action": a.key, "type": a.action_type},
                stage="execution",
                agent="execution",
            )
            await executor.run(s, a)
            await self._flush_tools(s, gw)
            await self._event(
                s,
                run,
                "tool_result",
                {"action": a.key, "status": a.status},
                stage="execution",
                agent="execution",
            )
            await s.commit()
            if self.after_stage:
                await self.after_stage(S.EXECUTING)
        await self._transition(s, req, run, S.VERIFYING)

    async def _stage_verify(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        req: Request,
        adapter: AgentFrameworkAdapter,
        gw: ToolGateway,
    ) -> None:
        actions = await self._actions(s, run.id)
        for a in actions:
            if a.status != ActionStatus.SUCCEEDED:
                continue
            ref = await locate(gw, a.action_type, a.params, a.idempotency_key)
            a.status = ActionStatus.VERIFIED if ref else ActionStatus.VERIFICATION_FAILED
            a.result = {**(a.result or {}), "verified_ref": ref}
        await s.flush()
        status, reason = _final_status(actions)
        await self._finish(s, run, req, adapter, status, reason)

    # ================================================================== helpers
    async def _finish(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        req: Request,
        adapter: AgentFrameworkAdapter,
        target: S,
        reason: str,
    ) -> None:
        """Run the reporting agent from actual execution records, then transition."""
        actions = await self._actions(s, run.id)
        cls = run.state.get("classification") or {}
        try:
            out = await self._agent(
                s,
                run,
                adapter,
                TaskType.REPORT,
                "reporting",
                {
                    "partner_name": cls.get("partner_name"),
                    "outcome": target.value,
                    "reason": reason,
                    "actions": [
                        {
                            "key": a.key,
                            "action_type": a.action_type,
                            "status": a.status,
                            "external_ref": (a.result or {}).get("verified_ref"),
                        }
                        for a in actions
                    ],
                },
            )
            _set(run, "report", out)
        except (StageError, ValidationError) as e:
            _set(run, "report", {"summary": f"report unavailable: {e}", "outcome": target})
        await self._transition(s, req, run, target, reason)
        run.status = _run_status(target)

    async def _gather_facts(
        self, gw: ToolGateway, partner_id: str | None, requester_id: str
    ) -> PartnerFacts:
        meta = await gw.call(agent="platform", tool="crm.meta", args={})
        users = await gw.call(agent="platform", tool="crm.list_users", args={})
        facts = PartnerFacts(
            world_date=date.fromisoformat(meta["world_date"]),
            restricted_regions=meta["restricted_regions"],
            active_user_ids={u["id"] for u in users if u["active"]},
            requester_id=requester_id,
        )
        if not partner_id:
            return facts
        try:
            partner = await gw.call(
                agent="platform", tool="crm.get_partner", args={"partner_id": partner_id}
            )
        except ConnectorError as e:
            if e.status == 404:
                return facts
            raise
        same = await gw.call(
            agent="platform", tool="crm.search_partners", args={"domain": partner["domain"]}
        )
        open_onb = await gw.call(
            agent="platform",
            tool="ticketing.search_tickets",
            args={"partner_id": partner_id, "type": "onboarding", "status": "open"},
        )
        facts.partner = partner
        facts.duplicate_partner_ids = [p["id"] for p in same if p["id"] != partner_id]
        facts.open_onboarding_ticket_ids = [t["id"] for t in open_onb]
        return facts

    def _task_request(self, run: WorkflowRun, t: TaskType, inp: dict[str, Any]) -> AgentTaskRequest:
        agent = AGENT_FOR_TASK[t]
        return AgentTaskRequest(
            task_type=t,
            run_id=run.id,
            input=inp,
            tool_manifest=sorted(AGENT_ALLOWLIST.get(agent, frozenset())),
            policy_snapshot={
                "medium_risk_requires_approval": self.settings.medium_risk_requires_approval
            },
            prompt_version=PROMPT_VERSIONS[agent],
            idempotency_key=f"{run.id}:{t.value}:{run.refinements}",
            trace_context={"run_id": str(run.id)},
        )

    async def _agent(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        adapter: AgentFrameworkAdapter,
        t: TaskType,
        stage: str,
        inp: dict[str, Any],
    ) -> dict[str, Any]:
        areq = self._task_request(run, t, inp)
        t0 = time.perf_counter()
        res = await adapter.execute(areq)
        return await self._persist_agent(
            s, run, adapter, stage, areq, res, (time.perf_counter() - t0) * 1000
        )

    async def _persist_agent(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        adapter: AgentFrameworkAdapter,
        stage: str,
        areq: AgentTaskRequest,
        res: AgentTaskResult,
        ms: float,
    ) -> dict[str, Any]:
        agent = AGENT_FOR_TASK[areq.task_type]
        ex = AgentExecution(
            id=areq.execution_id,
            run_id=run.id,
            stage=stage,
            agent=agent,
            framework=adapter.name,
            framework_version=adapter.version,
            prompt_version=areq.prompt_version,
            model=res.model or areq.model_config_.model,
            status=res.status,
            input=areq.input,
            output=res.output or None,
            error=res.error,
            input_tokens=res.usage.input_tokens,
            output_tokens=res.usage.output_tokens,
            cost_usd_estimate=res.usage.cost_usd_estimate,
            latency_ms=ms,
        )
        s.add(ex)
        await s.flush()
        for ev in res.events:
            await self._event(
                s, run, ev.type.value, ev.payload, stage=stage, agent=agent, execution_id=ex.id
            )
        await self._store_evidence(s, run, agent, res.evidence)
        if res.status is not ExecutionStatus.SUCCEEDED:
            raise StageError(f"{agent} agent failed: {res.error}")
        schema = STAGE_SCHEMAS.get(areq.task_type)
        if schema is not None:
            try:
                schema.model_validate(res.output)
            except ValidationError as e:
                ex.status = "failed"
                ex.error = f"schema validation failed: {e.error_count()} error(s)"
                raise StageError(f"{agent} output failed schema validation") from e
        return res.output

    async def _store_evidence(
        self, s: AsyncSession, run: WorkflowRun, agent: str, evidence: list[Evidence]
    ) -> None:
        existing = set(
            await s.scalars(select(EvidenceRecord.ref).where(EvidenceRecord.run_id == run.id))
        )
        for e in evidence:
            if e.ref in existing:
                continue
            existing.add(e.ref)
            s.add(
                EvidenceRecord(
                    run_id=run.id, ref=e.ref, source=e.source, summary=e.summary, collected_by=agent
                )
            )

    async def _flush_tools(self, s: AsyncSession, gw: ToolGateway) -> None:
        for r in gw.drain():
            s.add(
                ToolInvocation(
                    run_id=r.run_id,
                    agent=r.agent,
                    tool=r.tool,
                    status=r.status,
                    args_redacted=r.args_redacted,
                    result_redacted=r.result_redacted,
                    error=r.error,
                    latency_ms=r.latency_ms,
                )
            )

    async def _event(
        self,
        s: AsyncSession,
        run: WorkflowRun,
        event_type: str,
        payload: dict[str, Any],
        *,
        stage: str | None = None,
        agent: str | None = None,
        execution_id: uuid.UUID | None = None,
    ) -> None:
        seq = (
            await s.scalar(select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == run.id))
        ) or 0
        s.add(
            AgentEvent(
                run_id=run.id,
                execution_id=execution_id,
                seq=seq + 1,
                event_type=event_type,
                stage=stage,
                agent=agent,
                payload=payload,
            )
        )
        await s.flush()

    async def _transition(
        self, s: AsyncSession, req: Request, run: WorkflowRun, target: S, reason: str | None = None
    ) -> None:
        current = S(req.status)
        assert_transition(current, target)
        req.status = target
        req.status_reason = reason
        run.current_stage = target.value
        await self._event(
            s,
            run,
            "state_transition",
            {"from": current.value, "to": target.value, "reason": reason},
        )
        s.add(
            OutboxEvent(
                aggregate_type="request",
                aggregate_id=str(req.id),
                event_type="request.status_changed",
                payload={
                    "from": current.value,
                    "to": target.value,
                    "run_id": str(run.id),
                    "reason": reason,
                },
            )
        )

    async def _actions(self, s: AsyncSession, run_id: uuid.UUID) -> list[ProposedActionRecord]:
        rows = await s.scalars(
            select(ProposedActionRecord)
            .where(ProposedActionRecord.run_id == run_id)
            .order_by(ProposedActionRecord.key)
        )
        return list(rows)

    async def _load(self, s: AsyncSession, run_id: uuid.UUID) -> tuple[WorkflowRun, Request]:
        run = await s.get(WorkflowRun, run_id, populate_existing=True)
        if run is None:
            raise LookupError("run not found")
        req = await s.get(Request, run.request_id, populate_existing=True)
        assert req is not None
        return run, req

    async def _framework(self, run_id: uuid.UUID) -> str:
        async with self.sm() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None:
                raise LookupError("run not found")
            return run.framework


# ====================================================================== pure helpers
def _set(run: WorkflowRun, key: str, value: Any) -> None:
    run.state = {**(run.state or {}), key: value}  # reassign so the JSON column is dirtied


def _aware(dt: Any) -> Any:
    from datetime import UTC

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _run_status(status: S) -> RunStatus:
    if status in {S.COMPLETED, S.COMPLETED_WITH_WARNINGS}:
        return RunStatus.SUCCEEDED
    if status is S.FAILED:
        return RunStatus.FAILED
    if status is S.CANCELLED:
        return RunStatus.CANCELLED
    return RunStatus.PAUSED


def _topo(actions: list[ProposedActionRecord]) -> list[ProposedActionRecord]:
    by_key = {a.key: a for a in actions}
    out: list[ProposedActionRecord] = []
    seen: set[str] = set()

    def visit(a: ProposedActionRecord, stack: frozenset[str]) -> None:
        if a.key in seen or a.key in stack:
            return
        for d in a.depends_on:
            if d in by_key:
                visit(by_key[d], stack | {a.key})
        seen.add(a.key)
        out.append(a)

    for a in actions:
        visit(a, frozenset())
    return out


def _final_status(actions: list[ProposedActionRecord]) -> tuple[S, str]:
    st = {a.key: ActionStatus(a.status) for a in actions}
    verified = [a for a in actions if st[a.key] is ActionStatus.VERIFIED]
    broken = [
        a.key
        for a in actions
        if st[a.key] in {ActionStatus.FAILED, ActionStatus.VERIFICATION_FAILED}
    ]
    if broken:
        if verified:
            return S.COMPLETED_WITH_WARNINGS, f"actions failed or unverified: {broken}"
        return S.FAILED, f"error: no action succeeded ({broken})"
    if any(a.action_type == "request_compliance_doc" for a in verified):
        return S.AWAITING_REQUESTER_INFO, "waiting for SOC 2 Type II report from requester"
    if any(a.action_type == "escalate_to_human" for a in verified):
        return S.COMPLETED_WITH_WARNINGS, "escalated for human review"
    if any(st[a.key] in {ActionStatus.EXPIRED, ActionStatus.SKIPPED} for a in actions):
        return S.COMPLETED_WITH_WARNINGS, "some actions expired or were skipped"
    if not verified:
        return S.COMPLETED_WITH_WARNINGS, "no actions were executed"
    return S.COMPLETED, "all approved actions executed and verified"
