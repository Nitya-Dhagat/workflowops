"""Rule-based, LLM-free implementation of every agent task.

It is the Phase-1 reference implementation of the adapter contract and a floor for the
evaluation: whatever the LLM frameworks do must be compared against this and the LLM baseline.
It deliberately proposes the *standard* onboarding process and relies on the deterministic
policy engine to strip actions that violate policy.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from datetime import date
from typing import Any, Literal

from backend.domain.enums import Priority, RiskClass
from backend.domain.schemas import (
    ActionPlan,
    Classification,
    Evidence,
    Finding,
    InvestigationReport,
    ProposedAction,
    Report,
)
from backend.frameworks.base import (
    AGENT_FOR_TASK,
    AgentTaskRequest,
    AgentTaskResult,
    ExecutionStatus,
    PlatformEvent,
    PlatformEventType,
    ResumeSignal,
    TaskType,
    TokenUsage,
    ToolCallSummary,
)
from backend.tools.gateway import ToolGateway

ONBOARDING_PATTERNS = [
    r"\bonboard",
    r"\bset ?up .* as (a|our) partner",
    r"\badd .* as (a|our)? ?partner",
    r"\bnew partner\b",
    r"\bpartner (setup|registration)\b",
]
URGENT = re.compile(r"\b(urgent|asap|immediately|today)\b", re.I)
DATA_STEWARD = "u_11"
SECURITY_REVIEWER = "u_12"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class RulesAdapter:
    name = "rules"
    version = "1.0.0"

    def __init__(self, gateway: ToolGateway) -> None:
        self.gateway = gateway
        self._status: dict[str, ExecutionStatus] = {}

    # ------------------------------------------------------------------ contract
    async def execute(self, request: AgentTaskRequest) -> AgentTaskResult:
        agent = AGENT_FOR_TASK[request.task_type]
        self._status[str(request.execution_id)] = ExecutionStatus.RUNNING
        events = [PlatformEvent(type=PlatformEventType.AGENT_STARTED, agent=agent)]
        calls: list[ToolCallSummary] = []

        async def tool(name: str, **args: Any) -> Any:
            t0 = time.perf_counter()
            events.append(
                PlatformEvent(type=PlatformEventType.TOOL_CALL, agent=agent, payload={"tool": name})
            )
            try:
                out = await self.gateway.call(
                    agent=agent, tool=name, args=args, run_id=request.run_id
                )
            except Exception:
                calls.append(
                    ToolCallSummary(
                        tool=name, status="error", latency_ms=(time.perf_counter() - t0) * 1000
                    )
                )
                raise
            calls.append(
                ToolCallSummary(
                    tool=name, status="ok", latency_ms=(time.perf_counter() - t0) * 1000
                )
            )
            events.append(
                PlatformEvent(
                    type=PlatformEventType.TOOL_RESULT, agent=agent, payload={"tool": name}
                )
            )
            return out

        try:
            output, evidence = await self._dispatch(request, tool)
        except Exception as e:  # surfaced to the engine as a failed execution
            self._status[str(request.execution_id)] = ExecutionStatus.FAILED
            events.append(
                PlatformEvent(type=PlatformEventType.ERROR, agent=agent, payload={"error": str(e)})
            )
            usage, model = self._pop_usage(request)
            return AgentTaskResult(
                execution_id=request.execution_id,
                status=ExecutionStatus.FAILED,
                error=str(e),
                usage=usage,
                model=model,
                tool_calls=calls,
                events=events,
            )
        self._status[str(request.execution_id)] = ExecutionStatus.SUCCEEDED
        usage, model = self._pop_usage(request)
        if model:
            events.append(
                PlatformEvent(
                    type=PlatformEventType.MODEL_CALL,
                    agent=agent,
                    payload={"model": model},
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd_estimate=usage.cost_usd_estimate,
                )
            )
        events.append(PlatformEvent(type=PlatformEventType.AGENT_FINISHED, agent=agent))
        return AgentTaskResult(
            execution_id=request.execution_id,
            status=ExecutionStatus.SUCCEEDED,
            output=output,
            evidence=evidence,
            usage=usage,
            model=model,
            tool_calls=calls,
            events=events,
        )

    def _pop_usage(self, request: AgentTaskRequest) -> tuple[TokenUsage, str | None]:
        """Token usage for this execution (rule-based tasks use no model)."""
        return TokenUsage(), None

    async def stream(self, request: AgentTaskRequest) -> AsyncIterator[PlatformEvent]:
        result = await self.execute(request)
        for ev in result.events:
            yield ev

    async def get_status(self, execution_id: str) -> ExecutionStatus:
        return self._status.get(execution_id, ExecutionStatus.FAILED)

    async def cancel(self, execution_id: str) -> None:
        self._status[execution_id] = ExecutionStatus.CANCELLED

    async def resume(self, execution_id: str, signal: ResumeSignal) -> AgentTaskResult:
        # Rule-based tasks are single-shot; the platform engine owns pause/resume.
        import uuid

        return AgentTaskResult(
            execution_id=uuid.UUID(execution_id), status=ExecutionStatus.SUCCEEDED
        )

    # ------------------------------------------------------------------ tasks
    async def _dispatch(
        self, req: AgentTaskRequest, tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        match req.task_type:
            case TaskType.CLASSIFY:
                return await self._classify(req.input, tool)
            case TaskType.RESEARCH:
                return await self._research(req.input, tool)
            case TaskType.POLICY:
                return await self._policy(req.input, tool)
            case TaskType.DATA:
                return await self._data(req.input, tool)
            case TaskType.PLAN:
                return await self._plan(req.input, tool)
            case TaskType.VALIDATE:
                return {"advisory_issues": []}, []
            case TaskType.REPORT:
                return self._report(req.input), []
        raise ValueError(f"unsupported task {req.task_type}")

    async def _classify(
        self, inp: dict[str, Any], tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        text = inp["description"]
        low = text.lower()
        onboarding = any(re.search(p, low) for p in ONBOARDING_PATTERNS)
        urgency = (
            Priority.URGENT if URGENT.search(text) else Priority(inp.get("priority", "normal"))
        )
        if not onboarding:
            c = Classification(
                request_type="unknown",
                in_scope=False,
                urgency=urgency,
                rationale="no partner-onboarding intent detected",
            )
            return c.model_dump(), []
        partners = await tool("crm.search_partners")
        ntext = f" {_norm(text)} "
        matches = [p for p in partners if f" {_norm(p['name'])} " in ntext]
        matches.sort(key=lambda p: -len(p["name"]))
        if not matches:
            c = Classification(
                request_type="partner_onboarding",
                in_scope=True,
                missing_info=["partner_name"],
                urgency=urgency,
                rationale="onboarding intent but no known partner named",
            )
            return c.model_dump(), []
        p = matches[0]
        c = Classification(
            request_type="partner_onboarding",
            in_scope=True,
            partner_name=p["name"],
            partner_id=p["id"],
            urgency=urgency,
            needs_human_escalation=urgency is Priority.URGENT,
            rationale=f"matched CRM partner {p['id']}",
        )
        ev = Evidence(ref=f"crm:{p['id']}", source="crm", summary=f"CRM record for {p['name']}")
        return c.model_dump(), [ev]

    async def _research(
        self, inp: dict[str, Any], tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        hits = await tool("kb.search", q="partner onboarding requirements compliance access", k=6)
        evidence = [
            Evidence(ref=f"policy:{h['id']}", source="knowledge_base", summary=h["title"])
            for h in hits
            if h["status"] == "active"
        ]
        rep = InvestigationReport(
            agent="research",
            evidence=evidence,
            facts={"relevant_policies": [e.ref for e in evidence]},
        )
        return rep.model_dump(), evidence

    async def _policy(
        self, inp: dict[str, Any], tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        listing = await tool("kb.list_policies")
        findings: list[Finding] = []
        evidence: list[Evidence] = []
        for meta in listing:
            if meta["category"] not in {"onboarding", "compliance", "security", "data-quality"}:
                continue
            if meta["status"] == "superseded":
                findings.append(
                    Finding(
                        code="superseded_policy_ignored",
                        detail=f"{meta['id']} is superseded by {meta.get('superseded_by')}",
                        evidence_refs=[f"policy:{meta['id']}"],
                    )
                )
                continue
            pol = await tool("kb.get_policy", policy_id=meta["id"])
            evidence.append(
                Evidence(ref=f"policy:{pol['id']}", source="knowledge_base", summary=pol["title"])
            )
            findings.append(
                Finding(
                    code="applicable_policy",
                    detail=pol["title"],
                    evidence_refs=[f"policy:{pol['id']}"],
                    explicit_policy=True,
                )
            )
        rep = InvestigationReport(agent="policy", findings=findings, evidence=evidence)
        return rep.model_dump(), evidence

    async def _data(self, inp: dict[str, Any], tool: Any) -> tuple[dict[str, Any], list[Evidence]]:
        pid = inp["partner_id"]
        meta = await tool("crm.meta")
        today = date.fromisoformat(meta["world_date"])
        partner = await tool("crm.get_partner", partner_id=pid)
        same_domain = await tool("crm.search_partners", domain=partner["domain"])
        users = {u["id"]: u for u in await tool("crm.list_users")}
        open_onb = await tool(
            "ticketing.search_tickets", partner_id=pid, type="onboarding", status="open"
        )
        ref = f"crm:{pid}"
        evidence = [Evidence(ref=ref, source="crm", summary=f"Partner record {partner['name']}")]
        findings: list[Finding] = []

        def add(
            code: str,
            detail: str,
            severity: Literal["info", "warning", "blocking"] = "warning",
            refs: list[str] | None = None,
        ) -> None:
            findings.append(
                Finding(code=code, detail=detail, severity=severity, evidence_refs=refs or [ref])
            )

        socs = [c for c in partner["certifications"] if c["type"] == "SOC2_TYPE_II"]
        if not socs:
            add(
                "missing_soc2",
                "No SOC 2 Type II report on file",
                "blocking",
                [ref, "policy:POL-ONB-002"],
            )
        elif all(date.fromisoformat(c["expires"]) <= today for c in socs):
            add(
                "expired_soc2",
                "SOC 2 Type II report has expired",
                "blocking",
                [ref, "policy:POL-ONB-002"],
            )
        if partner["region"] in meta["restricted_regions"]:
            add(
                "restricted_region",
                f"Region {partner['region']} is restricted",
                "blocking",
                [ref, "policy:POL-ONB-007"],
            )
        if partner["account_status"] != "prospect":
            add(
                "not_onboardable",
                f"Account status is {partner['account_status']}",
                "blocking",
                [ref, "policy:POL-ONB-001"],
            )
        owner = users.get(partner["owner_id"])
        if owner is None or not owner["active"]:
            add(
                "inactive_owner",
                f"Owner {partner['owner_id']} is inactive",
                "warning",
                [ref, "policy:POL-ONB-004"],
            )
        dups = [p["id"] for p in same_domain if p["id"] != pid]
        if dups:
            add(
                "duplicate_partner",
                f"Records share domain: {dups}",
                "blocking",
                [ref, *[f"crm:{d}" for d in dups], "policy:POL-ONB-006"],
            )
            evidence += [
                Evidence(ref=f"crm:{d}", source="crm", summary="Duplicate record") for d in dups
            ]
        if sum(1 for c in partner["contacts"] if c["primary"]) > 1:
            add(
                "conflicting_contacts",
                "More than one primary contact",
                "blocking",
                [ref, "policy:POL-ONB-008"],
            )
        if open_onb:
            tids = [t["id"] for t in open_onb]
            add(
                "duplicate_submission",
                f"Open onboarding ticket(s): {tids}",
                "blocking",
                [*[f"ticket:{t}" for t in tids], "policy:POL-ONB-009"],
            )
            evidence += [
                Evidence(ref=f"ticket:{t}", source="ticketing", summary="Open onboarding ticket")
                for t in tids
            ]
        if partner["tier"] == "strategic":
            add(
                "strategic_tier",
                "Strategic partner: security review required",
                "info",
                [ref, "policy:POL-SEC-002"],
            )
        for doc in partner.get("documents", []):
            evidence.append(
                Evidence(
                    ref=f"crm:{pid}:doc:{doc['id']}",
                    source="crm",
                    summary=f"Partner-supplied document: {doc['title']}",
                    untrusted=True,
                )
            )
        rep = InvestigationReport(
            agent="data",
            findings=findings,
            evidence=evidence,
            facts={
                "partner_id": pid,
                "tier": partner["tier"],
                "soc2_status": "valid"
                if socs and any(date.fromisoformat(c["expires"]) > today for c in socs)
                else ("expired" if socs else "missing"),
                "region_restricted": partner["region"] in meta["restricted_regions"],
                "owner_id": partner["owner_id"],
                "primary_contact_ids": [c["id"] for c in partner["contacts"] if c["primary"]],
                "active_owner_ids": sorted(
                    u for u, v in users.items() if v["active"] and v.get("is_account_owner")
                ),
            },
        )
        return rep.model_dump(), evidence

    async def _plan(self, inp: dict[str, Any], tool: Any) -> tuple[dict[str, Any], list[Evidence]]:
        cls = Classification.model_validate(inp["classification"])
        data = InvestigationReport.model_validate(inp["reports"]["data"])
        codes = {f.code for f in data.findings}
        pid = cls.partner_id
        assert pid is not None
        ref = f"crm:{pid}"
        actions: list[ProposedAction] = []
        info_needed: list[str] = []

        def act(
            t: str,
            params: dict[str, Any],
            risk: str,
            refs: list[str],
            deps: list[str] | None = None,
            outcome: str = "",
        ) -> str:
            key = f"a{len(actions) + 1}"
            actions.append(
                ProposedAction(
                    key=key,
                    action_type=t,
                    params=params,
                    risk_class=RiskClass(risk),
                    evidence_refs=refs,
                    depends_on=deps or [],
                    expected_outcome=outcome,
                )
            )
            return key

        if "duplicate_submission" in codes:
            return ActionPlan(
                actions=[],
                rationale="Onboarding already in progress "
                "(POL-ONB-009); linking to existing ticket.",
            ).model_dump(), []

        owner = data.facts["owner_id"]
        owner_key: str | None = None
        if "inactive_owner" in codes:
            owner = data.facts["active_owner_ids"][0]
            owner_key = act(
                "assign_account_owner",
                {"partner_id": pid, "owner_id": owner},
                "medium",
                [ref, "policy:POL-ONB-004"],
                outcome="Active owner set",
            )
        task = act(
            "create_onboarding_task",
            {"partner_id": pid, "owner_id": owner, "title": f"Onboard partner {cls.partner_name}"},
            "medium",
            [ref, "policy:POL-ONB-003"],
            [owner_key] if owner_key else [],
            "Onboarding task exists in ticketing",
        )
        if codes & {"missing_soc2", "expired_soc2"}:
            info_needed.append("SOC2_TYPE_II report")
            act(
                "request_compliance_doc",
                {
                    "partner_id": pid,
                    "doc_type": "SOC2_TYPE_II",
                    "requester_id": inp["requester_id"],
                },
                "medium",
                [ref, "policy:POL-ONB-002", "policy:POL-CMP-001"],
                outcome="Requester asked for SOC 2 report",
            )
        for code in ("duplicate_partner", "conflicting_contacts"):
            if code in codes:
                act(
                    "escalate_to_human",
                    {
                        "partner_id": pid,
                        "finding_code": code,
                        "assignee_id": DATA_STEWARD,
                        "reason": next(f.detail for f in data.findings if f.code == code),
                    },
                    "medium",
                    [ref, "policy:POL-ESC-001"],
                    outcome="Escalation ticket open",
                )
        if "strategic_tier" in codes:
            act(
                "create_security_review_task",
                {"partner_id": pid, "owner_id": SECURITY_REVIEWER},
                "medium",
                [ref, "policy:POL-SEC-002"],
                outcome="Security review task exists",
            )
        access = act(
            "grant_portal_access",
            {"partner_id": pid, "access_level": "standard"},
            "high",
            [ref, "policy:POL-ONB-002", "policy:POL-SEC-001"],
            [task],
            "Partner has standard portal access",
        )
        contact = (data.facts["primary_contact_ids"] or [""])[0]
        act(
            "send_external_notification",
            {"partner_id": pid, "contact_id": contact, "template": "welcome"},
            "high",
            [ref, "policy:POL-ONB-005"],
            [access],
            "Welcome notification sent",
        )
        plan = ActionPlan(
            actions=actions,
            requires_requester_info=info_needed,
            rationale="Standard onboarding process (POL-PRC-001) adjusted for "
            f"findings: {sorted(codes) or 'none'}",
        )
        return plan.model_dump(), []

    def _report(self, inp: dict[str, Any]) -> dict[str, Any]:
        done = [
            f"{a['action_type']} ({a.get('external_ref') or 'no ref'})"
            for a in inp["actions"]
            if a["status"] == "verified"
        ]
        not_done = [
            f"{a['action_type']}: {a['status']}"
            for a in inp["actions"]
            if a["status"] != "verified"
        ]
        next_steps: list[str] = []
        if any(
            a["action_type"] == "request_compliance_doc" and a["status"] == "verified"
            for a in inp["actions"]
        ):
            next_steps.append("Provide the partner's SOC 2 Type II report to continue.")
        name = inp.get("partner_name") or "the requested partner"
        summary = (
            f"Onboarding request for {name}: {len(done)} action(s) completed and "
            f"verified, {len(not_done)} not performed. Outcome: {inp['outcome']}."
        )
        if inp.get("reason"):
            summary += f" Reason: {inp['reason']}."
        return Report(
            summary=summary,
            outcome=inp["outcome"],
            actions_done=done,
            actions_not_done=not_done,
            next_steps=next_steps,
        ).model_dump()
