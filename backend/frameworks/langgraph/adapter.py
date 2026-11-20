"""LangGraph framework (Mode A: stage executor), on the shared Gemini ``LLMClient``.

LangGraph-native pieces:
- planning is an explicit ``StateGraph``: ``draft`` (LLM) -> ``self_check`` (schema/evidence
  pre-check against the action catalog) -> conditional edge back to ``draft`` with feedback
  (max 2 revisions) or END. Compiled with an ``InMemorySaver`` checkpointer (thread per run).
- intake and reporting run as single-node graphs around the shared Gemini prompts.
The platform policy engine still validates the final plan; graph objects never leave here.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from backend.domain.schemas import ActionPlan, Evidence
from backend.frameworks.base import AgentTaskRequest, TaskType
from backend.frameworks.gemini.adapter import GeminiAdapter
from backend.policy.catalog import CATALOG

MAX_REVISIONS = 2


class PlanState(TypedDict, total=False):
    request: AgentTaskRequest
    plan: dict[str, Any]
    issues: list[str]
    revisions: int
    trace: Annotated[list[str], operator.add]


class TaskState(TypedDict, total=False):
    request: AgentTaskRequest
    tool: Any
    output: dict[str, Any]
    evidence: list[Evidence]


def precheck(plan: ActionPlan, known_evidence: set[str]) -> list[str]:
    """Framework-side self-check (catalog schema + evidence). Not a substitute for policy."""
    issues = []
    for a in plan.actions:
        spec = CATALOG.get(a.action_type)
        if spec is None:
            issues.append(f"{a.key}: unknown action type {a.action_type}")
            continue
        try:
            spec.params_model.model_validate(a.params)
        except ValidationError as e:
            issues.append(f"{a.key}: invalid params ({e.error_count()} errors)")
        missing = [r for r in a.evidence_refs if r not in known_evidence]
        if missing:
            issues.append(f"{a.key}: cites evidence that was never collected {missing}")
        if spec.risk.value == "high" and not a.evidence_refs:
            issues.append(f"{a.key}: high-risk action must cite evidence")
    return issues


class LangGraphAdapter(GeminiAdapter):
    name = "langgraph"
    version = "1.2.24"

    def __init__(self, gateway: Any, llm: Any) -> None:
        super().__init__(gateway, llm)
        self._checkpointer = InMemorySaver()
        self._plan_graph = self._build_plan_graph()
        self._task_graphs = {
            t: self._build_task_graph(t) for t in (TaskType.CLASSIFY, TaskType.REPORT)
        }

    # ------------------------------------------------------------------ graphs
    def _build_plan_graph(self) -> Any:
        async def draft(state: PlanState) -> PlanState:
            req = state["request"]
            feedback = [
                *req.input.get("feedback", []),
                *({"check": "self_check", "message": m} for m in state.get("issues", [])),
            ]
            sub = req.model_copy(update={"input": {**req.input, "feedback": feedback}})
            plan, _ = await GeminiAdapter._llm_plan(self, sub)
            return {"plan": plan, "revisions": state.get("revisions", -1) + 1, "trace": ["draft"]}

        def self_check(state: PlanState) -> PlanState:
            req = state["request"]
            known = {e["ref"] for r in req.input["reports"].values() for e in r.get("evidence", [])}
            known.add(f"crm:{req.input['classification'].get('partner_id')}")
            issues = precheck(ActionPlan.model_validate(state["plan"]), known)
            return {"issues": issues, "trace": [f"self_check:{len(issues)}"]}

        def route(state: PlanState) -> str:
            return "draft" if state["issues"] and state["revisions"] < MAX_REVISIONS else END

        g = StateGraph(PlanState)
        g.add_node("draft", draft)
        g.add_node("self_check", self_check)
        g.add_edge(START, "draft")
        g.add_edge("draft", "self_check")
        g.add_conditional_edges("self_check", route, ["draft", END])
        return g.compile(checkpointer=self._checkpointer)

    def _build_task_graph(self, task: TaskType) -> Any:
        async def node(state: TaskState) -> TaskState:
            req = state["request"]
            if task is TaskType.CLASSIFY:
                out, ev = await GeminiAdapter._llm_classify(self, req, state["tool"])
            else:
                out, ev = await GeminiAdapter._llm_report(self, req)
            return {"output": out, "evidence": ev}

        g = StateGraph(TaskState)
        g.add_node(task.value, node)
        g.add_edge(START, task.value)
        g.add_edge(task.value, END)
        return g.compile()

    # ------------------------------------------------------------------ dispatch
    async def _dispatch(
        self, req: AgentTaskRequest, tool: Any
    ) -> tuple[dict[str, Any], list[Evidence]]:
        if req.task_type is TaskType.PLAN:
            cfg = {"configurable": {"thread_id": f"{req.run_id}:{req.idempotency_key}"}}
            final = await self._plan_graph.ainvoke({"request": req, "trace": []}, cfg)
            plan = dict(final["plan"])
            plan["rationale"] = (
                plan.get("rationale") or ""
            ) + f" [langgraph: {' -> '.join(final['trace'])}]"
            return plan, []
        if req.task_type in self._task_graphs:
            final = await self._task_graphs[req.task_type].ainvoke({"request": req, "tool": tool})
            return final["output"], final.get("evidence", [])
        return await super(GeminiAdapter, self)._dispatch(req, tool)
