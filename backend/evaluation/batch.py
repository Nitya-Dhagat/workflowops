"""Resumable daily batches for live LLM evaluation under a small provider quota.

Each framework keeps one cumulative results file (``live-<framework>.json``). A batch runs the
next not-yet-done cases, rotating across frameworks so they progress evenly, one case at a time,
and stops the whole batch at the first provider-quota error. Quota-blocked runs are never stored.
Results from a different model or dataset version are never mixed into the same file.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.evaluation.cases import EvalCase
from backend.evaluation.report import is_infra_failure, is_quota_blocked

RunOne = Callable[[EvalCase, str], Awaitable[dict[str, Any]]]


class IncompatibleResultsError(Exception):
    pass


def load_or_init(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return {"meta": meta, "results": []}
    blob: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for key in ("model", "dataset_version", "scorer_version"):
        if blob["meta"].get(key) != meta[key]:
            raise IncompatibleResultsError(
                f"{path.name}: {key} {blob['meta'].get(key)!r} != {meta[key]!r}; "
                "start a new file instead of mixing versions"
            )
    return blob


def save(path: Path, blob: dict[str, Any]) -> None:
    path.write_text(json.dumps(blob, indent=1), encoding="utf-8", newline="\n")


async def run_batch(
    cases: list[EvalCase],
    frameworks: list[str],
    results_dir: Path,
    meta: dict[str, Any],
    run_one: RunOne,
    max_runs: int,
) -> dict[str, Any]:
    """Run up to ``max_runs`` new cases total; return a summary of what happened."""
    files = {fw: results_dir / f"live-{fw}.json" for fw in frameworks}
    blobs = {fw: load_or_init(files[fw], {**meta, "framework": fw}) for fw in frameworks}
    for blob in blobs.values():  # infra failures stored by older versions get another try
        blob["results"] = [r for r in blob["results"] if not is_infra_failure(r)]
    done = {fw: {r["case_id"] for r in blobs[fw]["results"]} for fw in frameworks}
    queues = {fw: [c for c in cases if c.id not in done[fw]] for fw in frameworks}
    ran: list[str] = []
    stopped_on_quota = False
    infra_skipped: list[str] = []
    while len(ran) < max_runs and any(queues.values()) and not stopped_on_quota:
        # Round-robin, least-progressed first, so frameworks stay even across days.
        for fw in sorted(frameworks, key=lambda f: len(blobs[f]["results"])):
            if len(ran) >= max_runs or not queues[fw]:
                continue
            case = queues[fw].pop(0)
            result = await run_one(case, fw)
            if is_quota_blocked(result):
                stopped_on_quota = True  # don't waste the rest of the day's calls
                break
            if is_infra_failure(result):
                infra_skipped.append(f"{fw}:{case.id}")  # not a quality result; retry next batch
                continue
            blobs[fw]["results"].append(result)
            save(files[fw], blobs[fw])
            ran.append(f"{fw}:{case.id}")
    return {
        "ran": ran,
        "stopped_on_quota": stopped_on_quota,
        "infra_skipped": infra_skipped,
        "progress": {fw: f"{len(blobs[fw]['results'])}/{len(cases)}" for fw in frameworks},
    }
