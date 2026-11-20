"""Dataset lint: schema-valid, unique, reproducible, covers every trap, no answer leakage."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from backend.core.config import REPO_ROOT
from backend.evaluation.cases import load_cases
from backend.policy.catalog import CATALOG
from scripts.generate_eval_cases import generate


def test_cases_are_valid_unique_and_balanced() -> None:
    cases = load_cases()
    assert len(cases) >= 30
    assert len({c.id for c in cases}) == len(cases)
    cats = Counter(c.category for c in cases)
    assert len(cats) == 10 and min(cats.values()) >= 2
    dev = sum(c.split == "dev" for c in cases)
    assert 0.2 <= dev / len(cases) <= 0.4
    for c in cases:
        for a in c.expect.required_actions + c.expect.prohibited_actions:
            assert a in CATALOG, (c.id, a)
        assert c.expect.final_status not in c.request.lower()  # no label leakage


def test_every_trap_is_exercised() -> None:
    traps = yaml.safe_load((REPO_ROOT / "data/seed/traps.yaml").read_text())["traps"]
    used = {t for c in load_cases() for t in c.traps}
    assert {t["id"] for t in traps} <= used


def test_generation_is_reproducible(tmp_path: Path) -> None:
    fresh = generate()
    committed = [
        yaml.safe_load(p.read_text(encoding="utf-8"))
        for p in sorted((REPO_ROOT / "data/evaluation/cases").glob("*.yaml"))
    ]
    assert fresh == committed
