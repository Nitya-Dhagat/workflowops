from __future__ import annotations

import random
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from backend.core.config import REPO_ROOT
from backend.core.logging import REDACTED, redact
from backend.orchestration.executor import RetryPolicy
from mock_business_systems.search import BM25
from scripts.generate_world import generate


def test_redacts_secrets_by_key_and_pii_by_pattern() -> None:
    out = redact(
        {
            "api_key": "sk-123",
            "nested": {"Password": "x"},
            "note": "mail bob@corp.example or call +1-555-123-4567",
            "items": [{"token": "t"}],
        }
    )
    assert out["api_key"] == REDACTED and out["nested"]["Password"] == REDACTED
    assert "bob@" not in out["note"] and "555-123" not in out["note"]
    assert out["items"][0]["token"] == REDACTED


@given(st.integers(min_value=1, max_value=20), st.integers(min_value=0, max_value=10_000))
def test_retry_delay_is_bounded(attempt: int, seed: int) -> None:
    p = RetryPolicy(max_attempts=5, base_delay_s=0.2, max_delay_s=3.0)
    d = p.delay(attempt, random.Random(seed))
    assert 0 <= d <= 3.0


def test_bm25_ranks_relevant_policy_first() -> None:
    idx = BM25()
    idx.add("a", "SOC 2 Type II report required before portal access")
    idx.add("b", "Data retention for rejected requests")
    assert idx.search("soc 2 report access")[0][0] == "a"


def test_world_generation_is_byte_reproducible(tmp_path: Path) -> None:
    generate(1234, tmp_path / "seed", tmp_path / "policies")
    for rel in ("world.json", "traps.yaml"):
        assert (tmp_path / "seed" / rel).read_bytes() == (
            REPO_ROOT / "data/seed" / rel
        ).read_bytes()
    gen = sorted(p.name for p in (tmp_path / "policies").glob("*.md"))
    committed = sorted(p.name for p in (REPO_ROOT / "data/policies").glob("*.md"))
    assert gen == committed and len(gen) == 30
    for name in gen:
        assert (tmp_path / "policies" / name).read_bytes() == (
            REPO_ROOT / "data/policies" / name
        ).read_bytes()


def test_world_has_planted_traps(world) -> None:  # type: ignore[no-untyped-def]
    import yaml

    traps = yaml.safe_load((REPO_ROOT / "data/seed/traps.yaml").read_text())["traps"]
    ids = {t["id"] for t in traps}
    assert {
        "trap-missing-soc2",
        "trap-expired-cert",
        "trap-duplicate-partner",
        "trap-conflicting-records",
        "trap-inactive-owner",
        "trap-restricted-region",
        "trap-contradicting-policies",
        "trap-injection-doc",
        "trap-duplicate-submission",
    } <= ids
    pids = {p["id"] for p in world["partners"]}
    assert len(pids) == 50
    assert all(set(t["partner_ids"]) <= pids for t in traps)
