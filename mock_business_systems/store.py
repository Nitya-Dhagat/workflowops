"""In-memory state for the simulated CRM, ticketing, knowledge base and notifications."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from mock_business_systems.search import BM25


def parse_policy(text: str) -> dict[str, Any]:
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    meta["body"] = body.strip()
    return dict(meta)


class WorldStore:
    def __init__(self, world_path: Path, policy_dir: Path) -> None:
        self.world_path = world_path
        self.policy_dir = policy_dir
        self.honor_idempotency = True
        self.reset()

    def reset(self) -> None:
        raw = json.loads(self.world_path.read_text(encoding="utf-8"))
        self.meta = {
            "version": raw["version"],
            "world_date": raw["world_date"],
            "restricted_regions": raw["restricted_regions"],
        }
        self.users: dict[str, dict[str, Any]] = {u["id"]: u for u in raw["users"]}
        self.partners: dict[str, dict[str, Any]] = {p["id"]: p for p in raw["partners"]}
        for p in self.partners.values():
            p.setdefault("audit", [])
        self.tickets: dict[str, dict[str, Any]] = {t["id"]: t for t in raw["tickets"]}
        self.notifications: list[dict[str, Any]] = []
        self.policies: dict[str, dict[str, Any]] = {}
        self.index = BM25()
        for f in sorted(self.policy_dir.glob("*.md")):
            pol = parse_policy(f.read_text(encoding="utf-8"))
            self.policies[pol["id"]] = pol
            self.index.add(pol["id"], f"{pol['title']} {pol['body']}")
        self._ticket_seq = len(self.tickets)
        self._notif_seq = 0
        self.write_count = 0  # number of side effects actually applied (for tests)

    # ------------------------------------------------------------------ helpers
    def next_ticket_id(self) -> str:
        self._ticket_seq += 1
        return f"TCK-{self._ticket_seq:05d}"

    def next_notification_id(self) -> str:
        self._notif_seq += 1
        return f"NTF-{self._notif_seq:05d}"

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "meta": self.meta,
                "partners": self.partners,
                "tickets": self.tickets,
                "notifications": self.notifications,
                "write_count": self.write_count,
            }
        )
