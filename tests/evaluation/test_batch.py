"""Daily batch runner: resumes, rotates frameworks, stops on quota, never mixes models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.evaluation.batch import IncompatibleResultsError, run_batch
from backend.evaluation.cases import EvalCase, load_cases

META = {"dataset_version": "v1", "scorer_version": "1.0", "model": "m1"}


def ok(case: EvalCase, fw: str) -> dict[str, Any]:
    return {"case_id": case.id, "framework": fw, "success": True, "status_reason": ""}


async def test_resumes_and_rotates(tmp_path: Path) -> None:
    cases = load_cases()[:4]
    calls: list[str] = []

    async def run_one(case: EvalCase, fw: str) -> dict[str, Any]:
        calls.append(f"{fw}:{case.id}")
        return ok(case, fw)

    out = await run_batch(cases, ["a", "b"], tmp_path, META, run_one, max_runs=3)
    assert calls == [f"a:{cases[0].id}", f"b:{cases[0].id}", f"a:{cases[1].id}"]
    assert out["progress"] == {"a": "2/4", "b": "1/4"}
    out = await run_batch(cases, ["a", "b"], tmp_path, META, run_one, max_runs=2)
    assert calls[3:] == [f"b:{cases[1].id}", f"a:{cases[2].id}"]  # picks up where it left off


async def test_stops_on_quota_and_does_not_store_it(tmp_path: Path) -> None:
    cases = load_cases()[:3]
    seq = iter([False, True, False])

    async def run_one(case: EvalCase, fw: str) -> dict[str, Any]:
        r = ok(case, fw)
        if next(seq):
            r["status_reason"] = "error: 429 RESOURCE_EXHAUSTED quota"
        return r

    out = await run_batch(cases, ["a"], tmp_path, META, run_one, max_runs=5)
    assert out["stopped_on_quota"] and out["progress"] == {"a": "1/3"}


async def test_refuses_to_mix_models(tmp_path: Path) -> None:
    cases = load_cases()[:1]

    async def run_one(case: EvalCase, fw: str) -> dict[str, Any]:
        return ok(case, fw)

    await run_batch(cases, ["a"], tmp_path, META, run_one, max_runs=1)
    with pytest.raises(IncompatibleResultsError):
        await run_batch(cases, ["a"], tmp_path, {**META, "model": "m2"}, run_one, max_runs=1)
