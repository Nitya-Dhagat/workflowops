"""Deterministic policy & safety engine. No LLM is involved, and it is authoritative.

Checks, in order: allowlist -> schema/required fields -> scope/permissions -> business rules
-> duplicates -> dependency integrity -> risk & approval requirement.
Text that originates from data (requests, documents) is never interpreted here.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from backend.domain.enums import RiskClass
from backend.domain.schemas import ActionPlan, ProposedAction, ValidationIssue, ValidationReport
from backend.policy.catalog import CATALOG, canonical_json
from backend.policy.facts import PartnerFacts


@dataclass(frozen=True)
class PolicyConfig:
    medium_risk_requires_approval: bool = False


def _issue(key: str | None, check: str, msg: str) -> ValidationIssue:
    return ValidationIssue(action_key=key, check=check, message=msg, blocking=True)


def _business_rules(a: ProposedAction, f: PartnerFacts) -> list[ValidationIssue]:
    k, p, t = a.key, a.params, a.action_type
    out: list[ValidationIssue] = []

    def rule(cond: bool, msg: str) -> None:
        if cond:
            out.append(_issue(k, "business_rule", msg))

    pid = p.get("partner_id")
    if pid is not None:
        rule(f.partner_id is None, "no verified partner record for this request (POL-ONB-001)")
        rule(
            f.partner_id is not None and pid != f.partner_id,
            f"action targets {pid}, but the request concerns {f.partner_id}",
        )
    rule(f.restricted(), "partner is in a restricted region; no actions allowed (POL-ONB-007)")

    if t == "create_onboarding_task":
        rule(
            not f.onboardable_status(),
            "partner is not in `prospect` status and cannot be onboarded (POL-ONB-001)",
        )
        rule(
            f.is_duplicate_submission(),
            "an onboarding ticket is already open for this partner (POL-ONB-009)",
        )
        rule(
            p.get("owner_id") not in f.active_user_ids,
            "onboarding task owner must be an active user (POL-ONB-003)",
        )
    elif t == "assign_account_owner":
        rule(p.get("owner_id") not in f.active_user_ids, "new owner must be active (POL-ONB-004)")
    elif t == "grant_portal_access":
        rule(
            not f.soc2_valid(),
            f"SOC 2 Type II report is {f.soc2_status()}; access not allowed (POL-ONB-002)",
        )
        rule(
            p.get("access_level") != "standard",
            "automation may only grant `standard` access (POL-SEC-001)",
        )
        rule(f.has_duplicate_record(), "duplicate partner record unresolved (POL-ONB-006)")
        rule(f.is_duplicate_submission(), "duplicate onboarding submission (POL-ONB-009)")
        rule(not f.onboardable_status(), "partner is not onboardable (POL-ONB-001)")
    elif t == "send_external_notification":
        rule(not f.soc2_valid(), "compliance evidence not satisfied (POL-ONB-005)")
        rule(f.has_contact_conflict(), "conflicting primary contacts (POL-ONB-008)")
        rule(f.has_duplicate_record(), "duplicate partner record unresolved (POL-ONB-006)")
        rule(f.is_duplicate_submission(), "duplicate onboarding submission (POL-ONB-009)")
        rule(not f.onboardable_status(), "partner is not onboardable (POL-ONB-001)")
        contact_ids = {c["id"] for c in (f.partner or {}).get("contacts", [])}
        rule(p.get("contact_id") not in contact_ids, "contact does not belong to the partner")
    elif t == "request_compliance_doc":
        rule(f.soc2_valid(), "SOC 2 report is already valid; request is unsupported")
        rule(
            p.get("requester_id") != f.requester_id,
            "compliance requests go to the internal requester only (POL-CMP-001)",
        )
    elif t == "escalate_to_human":
        rule(p.get("assignee_id") not in f.active_user_ids, "assignee must be an active user")
    elif t == "create_security_review_task":
        rule(p.get("owner_id") not in f.active_user_ids, "owner must be an active user")
    return out


def validate_plan(
    plan: ActionPlan,
    facts: PartnerFacts,
    known_evidence: set[str],
    config: PolicyConfig | None = None,
) -> ValidationReport:
    config = config or PolicyConfig()
    report = ValidationReport()
    keys = [a.key for a in plan.actions]
    seen_payloads: set[str] = set()

    for a in plan.actions:
        issues: list[ValidationIssue] = []
        spec = CATALOG.get(a.action_type)
        if spec is None:
            issues.append(_issue(a.key, "allowlist", f"unknown action type `{a.action_type}`"))
        else:
            try:
                spec.params_model.model_validate(a.params)
            except ValidationError as e:
                fields = sorted({".".join(map(str, err["loc"])) for err in e.errors()})
                issues.append(_issue(a.key, "schema", f"invalid params: {', '.join(fields)}"))
            if not issues:
                issues.extend(_business_rules(a, facts))
            payload = canonical_json({"t": a.action_type, "p": a.params})
            if payload in seen_payloads:
                issues.append(_issue(a.key, "duplicate", "duplicate action in plan"))
            seen_payloads.add(payload)
            if spec.risk is RiskClass.HIGH:
                if not a.evidence_refs:
                    issues.append(_issue(a.key, "evidence", "high-risk action cites no evidence"))
                unknown = [r for r in a.evidence_refs if r not in known_evidence]
                if unknown:
                    issues.append(
                        _issue(a.key, "evidence", f"cites evidence never collected: {unknown}")
                    )
        if keys.count(a.key) > 1:
            issues.append(_issue(a.key, "schema", "duplicate action key"))
        for dep in a.depends_on:
            if dep not in keys or dep == a.key:
                issues.append(_issue(a.key, "dependency", f"unknown dependency `{dep}`"))

        report.issues.extend(issues)
        if issues:
            report.rejected.append(a.key)
            continue
        assert spec is not None
        report.accepted.append(a.key)
        report.effective_risk[a.key] = spec.risk
        if spec.risk is RiskClass.HIGH or (
            spec.risk is RiskClass.MEDIUM and config.medium_risk_requires_approval
        ):
            report.needs_approval.append(a.key)

    # An accepted action that depends on a rejected one cannot run either.
    changed = True
    while changed:
        changed = False
        by_key = {a.key: a for a in plan.actions}
        for k in list(report.accepted):
            bad = [d for d in by_key[k].depends_on if d in report.rejected]
            if bad:
                report.accepted.remove(k)
                report.rejected.append(k)
                report.effective_risk.pop(k, None)
                if k in report.needs_approval:
                    report.needs_approval.remove(k)
                report.issues.append(_issue(k, "dependency", f"depends on rejected {bad}"))
                changed = True
    return report
