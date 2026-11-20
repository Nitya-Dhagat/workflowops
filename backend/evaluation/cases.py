"""Evaluation case schema and loader (cases are synthetic and labeled by construction)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "normal",
    "missing_document",
    "incomplete",
    "out_of_scope",
    "should_reject",
    "conflicting_data",
    "inactive_owner",
    "duplicate_submission",
    "prompt_injection",
    "integration_failure",
]


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: Literal["partner_onboarding", "unknown"]
    in_scope: bool
    partner_id: str | None = None
    required_actions: list[str] = Field(default_factory=list)  # must end up verified
    prohibited_actions: list[str] = Field(default_factory=list)  # must never execute
    approval_required: bool = False  # a human approval must be requested
    final_status: str


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    category: Category
    split: Literal["dev", "test"]
    world_ref: str = "seed-v1"
    request: str
    requester: str = "u_01"
    attachments: list[dict[str, str]] = Field(default_factory=list)
    traps: list[str] = Field(default_factory=list)
    faults: list[dict[str, Any]] = Field(default_factory=list)  # mock-system fault rules
    expect: Expect
    reviewed: bool = True  # labels are derived by construction, not by an LLM


CASES_DIR = Path(__file__).resolve().parents[2] / "data" / "evaluation" / "cases"


def load_cases(directory: Path = CASES_DIR) -> list[EvalCase]:
    return [
        EvalCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(directory.glob("*.yaml"))
    ]


def dataset_version(directory: Path = CASES_DIR) -> str:
    h = hashlib.sha256()
    for p in sorted(directory.glob("*.yaml")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]
