"""Authoritative facts the policy engine evaluates against.

Facts are gathered by the platform from the systems of record, never taken from agent output.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class PartnerFacts(BaseModel):
    partner: dict[str, Any] | None = None
    world_date: date
    restricted_regions: list[str] = Field(default_factory=list)
    active_user_ids: set[str] = Field(default_factory=set)
    duplicate_partner_ids: list[str] = Field(default_factory=list)
    open_onboarding_ticket_ids: list[str] = Field(default_factory=list)
    requester_id: str = ""

    # ---------------------------------------------------------------- derived checks
    @property
    def partner_id(self) -> str | None:
        return self.partner["id"] if self.partner else None

    def soc2_valid(self) -> bool:
        """POL-ONB-002 + POL-CMP-002: unexpired SOC 2 Type II only (ISO 27001 not enough)."""
        if not self.partner:
            return False
        return any(
            c["type"] == "SOC2_TYPE_II" and date.fromisoformat(c["expires"]) > self.world_date
            for c in self.partner.get("certifications", [])
        )

    def soc2_status(self) -> str:
        if not self.partner:
            return "unknown"
        certs = [c for c in self.partner.get("certifications", []) if c["type"] == "SOC2_TYPE_II"]
        if not certs:
            return "missing"
        return "valid" if self.soc2_valid() else "expired"

    def restricted(self) -> bool:
        return bool(self.partner and self.partner["region"] in self.restricted_regions)

    def owner_active(self) -> bool:
        return bool(self.partner and self.partner["owner_id"] in self.active_user_ids)

    def primary_contacts(self) -> list[dict[str, Any]]:
        if not self.partner:
            return []
        return [c for c in self.partner.get("contacts", []) if c.get("primary")]

    def has_contact_conflict(self) -> bool:
        return len(self.primary_contacts()) > 1

    def has_duplicate_record(self) -> bool:
        return bool(self.duplicate_partner_ids)

    def is_duplicate_submission(self) -> bool:
        return bool(self.open_onboarding_ticket_ids)

    def onboardable_status(self) -> bool:
        return bool(self.partner and self.partner["account_status"] == "prospect")
