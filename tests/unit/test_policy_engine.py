from __future__ import annotations

from datetime import date
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.domain.enums import RiskClass
from backend.domain.schemas import ActionPlan, ProposedAction
from backend.policy.catalog import CATALOG, action_hash
from backend.policy.engine import PolicyConfig, validate_plan
from backend.policy.facts import PartnerFacts


def facts_for(world: dict[str, Any], pid: str, requester: str = "u_01") -> PartnerFacts:
    partner = next(p for p in world["partners"] if p["id"] == pid)
    dups = [
        p["id"] for p in world["partners"] if p["domain"] == partner["domain"] and p["id"] != pid
    ]
    open_onb = [
        t["id"]
        for t in world["tickets"]
        if t["partner_id"] == pid and t["type"] == "onboarding" and t["status"] == "open"
    ]
    return PartnerFacts(
        partner=partner,
        world_date=date.fromisoformat(world["world_date"]),
        restricted_regions=world["restricted_regions"],
        active_user_ids={u["id"] for u in world["users"] if u["active"]},
        duplicate_partner_ids=dups,
        open_onboarding_ticket_ids=open_onb,
        requester_id=requester,
    )


def pid_by_name(world: dict[str, Any], name: str) -> str:
    return next(p["id"] for p in world["partners"] if p["name"] == name)


def act(
    key: str,
    t: str,
    params: dict[str, Any],
    refs: list[str] | None = None,
    deps: list[str] | None = None,
    risk: RiskClass = RiskClass.LOW,
) -> ProposedAction:
    return ProposedAction(
        key=key,
        action_type=t,
        params=params,
        risk_class=risk,
        evidence_refs=refs or [],
        depends_on=deps or [],
    )


EVID = {"crm:partner-0011", "policy:POL-ONB-002", "crm:partner-0001", "crm:partner-0009"}


def test_clean_partner_access_needs_approval_even_if_planner_says_low(world) -> None:
    pid = pid_by_name(world, "Juniper Logic")
    f = facts_for(world, pid)
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "grant_portal_access",
                {"partner_id": pid, "access_level": "standard"},
                [f"crm:{pid}"],
                risk=RiskClass.LOW,
            )
        ]
    )
    r = validate_plan(plan, f, {f"crm:{pid}"})
    assert r.accepted == ["a1"]
    assert r.effective_risk["a1"] is RiskClass.HIGH
    assert r.needs_approval == ["a1"]


def test_missing_soc2_blocks_access_and_notification(world) -> None:
    pid = pid_by_name(world, "Northwind Cloud")
    f = facts_for(world, pid)
    contact = f.primary_contacts()[0]["id"]
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "grant_portal_access",
                {"partner_id": pid, "access_level": "standard"},
                [f"crm:{pid}"],
            ),
            act(
                "a2",
                "send_external_notification",
                {"partner_id": pid, "contact_id": contact, "template": "welcome"},
                [f"crm:{pid}"],
            ),
            act(
                "a3",
                "request_compliance_doc",
                {"partner_id": pid, "doc_type": "SOC2_TYPE_II", "requester_id": "u_01"},
            ),
        ]
    )
    r = validate_plan(plan, f, EVID)
    assert set(r.rejected) == {"a1", "a2"}
    assert r.accepted == ["a3"]
    assert any("POL-ONB-002" in i.message for i in r.issues if i.action_key == "a1")


def test_expired_and_iso_only_certs_are_not_valid(world) -> None:
    for name in ("Bluefin Analytics", "Granite Labs"):
        assert not facts_for(world, pid_by_name(world, name)).soc2_valid(), name
    assert facts_for(world, pid_by_name(world, "Juniper Logic")).soc2_valid()


def test_admin_access_never_allowed(world) -> None:
    pid = pid_by_name(world, "Juniper Logic")
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "grant_portal_access",
                {"partner_id": pid, "access_level": "admin"},
                [f"crm:{pid}"],
            )
        ]
    )
    r = validate_plan(plan, facts_for(world, pid), {f"crm:{pid}"})
    assert r.rejected == ["a1"]


def test_unknown_tool_and_bad_schema_rejected(world) -> None:
    pid = pid_by_name(world, "Juniper Logic")
    plan = ActionPlan(
        actions=[
            act("a1", "delete_partner", {"partner_id": pid}),
            act("a2", "create_onboarding_task", {"partner_id": pid}),  # missing owner/title
        ]
    )
    r = validate_plan(plan, facts_for(world, pid), set())
    assert r.rejected == ["a1", "a2"]
    checks = {i.action_key: i.check for i in r.issues}
    assert checks == {"a1": "allowlist", "a2": "schema"}


def test_high_risk_requires_collected_evidence(world) -> None:
    pid = pid_by_name(world, "Juniper Logic")
    plan = ActionPlan(
        actions=[
            act("a1", "grant_portal_access", {"partner_id": pid, "access_level": "standard"}),
            act(
                "a2",
                "grant_portal_access",
                {"partner_id": pid, "access_level": "standard"},
                ["policy:MADE-UP"],
            ),
        ]
    )
    r = validate_plan(plan, facts_for(world, pid), set())
    assert set(r.rejected) == {"a1", "a2"}


def test_cannot_target_another_partner(world) -> None:
    pid = pid_by_name(world, "Juniper Logic")
    other = pid_by_name(world, "Northwind Cloud")
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "create_onboarding_task",
                {"partner_id": other, "owner_id": "u_10", "title": "Onboard"},
            )
        ]
    )
    assert validate_plan(plan, facts_for(world, pid), set()).rejected == ["a1"]


def test_rejection_propagates_to_dependents(world) -> None:
    pid = pid_by_name(world, "Northwind Cloud")
    f = facts_for(world, pid)
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "grant_portal_access",
                {"partner_id": pid, "access_level": "standard"},
                [f"crm:{pid}"],
            ),
            act(
                "a2",
                "escalate_to_human",
                {
                    "partner_id": pid,
                    "finding_code": "x",
                    "reason": "test reason",
                    "assignee_id": "u_11",
                },
                deps=["a1"],
            ),
        ]
    )
    r = validate_plan(plan, f, EVID)
    assert set(r.rejected) == {"a1", "a2"}


def test_medium_risk_approval_is_configurable(world) -> None:
    pid = pid_by_name(world, "Juniper Logic")
    owner = facts_for(world, pid).partner["owner_id"]  # type: ignore[index]
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "create_onboarding_task",
                {"partner_id": pid, "owner_id": owner, "title": "Onboard"},
            )
        ]
    )
    f = facts_for(world, pid)
    assert validate_plan(plan, f, set()).needs_approval == []
    cfg = PolicyConfig(medium_risk_requires_approval=True)
    assert validate_plan(plan, f, set(), cfg).needs_approval == ["a1"]


def test_restricted_region_blocks_everything(world) -> None:
    pid = pid_by_name(world, "Fjord Networks")
    plan = ActionPlan(
        actions=[
            act(
                "a1",
                "escalate_to_human",
                {"partner_id": pid, "finding_code": "x", "reason": "abc", "assignee_id": "u_11"},
            )
        ]
    )
    assert validate_plan(plan, facts_for(world, pid), set()).rejected == ["a1"]


def test_action_hash_changes_with_params() -> None:
    a = action_hash("grant_portal_access", {"partner_id": "p", "access_level": "standard"})
    b = action_hash("grant_portal_access", {"partner_id": "p", "access_level": "admin"})
    assert a != b and len(a) == 64


# ------------------------------------------------------------------ property tests
param_values = st.one_of(
    st.text(max_size=12),
    st.integers(),
    st.none(),
    st.sampled_from(["standard", "admin", "u_10", "partner-0011", "SOC2_TYPE_II", "welcome"]),
)
random_action = st.builds(
    lambda t, keys, vals, refs: ProposedAction(
        key="a1",
        action_type=t,
        params=dict(zip(keys, vals, strict=False)),
        risk_class=RiskClass.LOW,
        evidence_refs=refs,
    ),
    st.one_of(st.sampled_from(sorted(CATALOG)), st.text(max_size=20)),
    st.lists(
        st.sampled_from(
            [
                "partner_id",
                "owner_id",
                "title",
                "access_level",
                "contact_id",
                "template",
                "doc_type",
                "requester_id",
                "x",
            ]
        ),
        max_size=5,
        unique=True,
    ),
    st.lists(param_values, min_size=5, max_size=5),
    st.lists(st.sampled_from([*sorted(EVID), "policy:FAKE"]), max_size=2),
)


@settings(max_examples=300, deadline=None)
@given(
    random_action,
    st.sampled_from(
        ["Juniper Logic", "Northwind Cloud", "Helios Security", "Fjord Networks", "Cobalt Systems"]
    ),
)
def test_property_only_catalog_valid_actions_pass_and_high_always_needs_approval(
    world: dict[str, Any], a: ProposedAction, name: str
) -> None:
    f = facts_for(world, pid_by_name(world, name))
    r = validate_plan(ActionPlan(actions=[a]), f, EVID)
    if "a1" in r.accepted:
        spec = CATALOG[a.action_type]
        spec.params_model.model_validate(a.params)  # must be schema-valid
        assert not f.restricted()
        if spec.risk is RiskClass.HIGH:
            assert "a1" in r.needs_approval
            assert a.params.get("access_level", "standard") == "standard"
            assert f.soc2_valid()
    else:
        assert "a1" in r.rejected
