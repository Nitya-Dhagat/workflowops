"""Run the evaluation and regenerate docs/benchmarks/REPORT.md.

Examples:
  uv run python scripts/run_eval.py --frameworks rules --trials 3          # offline, free
  uv run python scripts/run_eval.py --frameworks gemini,adk --trials 1     # live Gemini calls
  uv run python scripts/run_eval.py --report-only                          # rebuild report

Results are stored as JSON (with config, dataset version and git SHA) in
docs/benchmarks/results/; the report combines the latest result file per framework.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.config import REPO_ROOT, Settings
from backend.evaluation.batch import run_batch
from backend.evaluation.cases import EvalCase, dataset_version, load_cases
from backend.evaluation.report import write_report
from backend.evaluation.runner import SCORER_VERSION, run_all

RESULTS = REPO_ROOT / "docs" / "benchmarks" / "results"


def git_sha() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() or "unknown"


def latest_per_framework() -> list[Path]:
    latest: dict[str, Path] = {}
    for f in sorted(RESULTS.glob("*.json")):
        fw = json.loads(f.read_text(encoding="utf-8"))["meta"]["framework"]
        latest[fw] = f  # files sort by timestamp, so the last one wins
    return list(latest.values())


def daily(args: argparse.Namespace) -> None:
    settings = Settings()
    cases = [c for c in load_cases() if args.split == "all" or c.split == args.split]
    fws = [f for f in args.frameworks.split(",") if f != "rules"]
    meta = {
        "dataset_version": dataset_version(),
        "cases": len(cases),
        "split": args.split,
        "trials": 1,
        "scorer_version": SCORER_VERSION,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "git_sha": git_sha(),
        "started": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
    }

    async def one(case: EvalCase, fw: str) -> dict[str, Any]:
        return (await run_all([case], [fw], 1, 1))[0]

    out = asyncio.run(run_batch(cases, fws, RESULTS, meta, one, args.max_runs))
    print(f"ran {len(out['ran'])}: {', '.join(out['ran']) or 'nothing'}")
    print("progress:", out["progress"])
    if out["stopped_on_quota"]:
        print("stopped: Gemini daily quota reached; run again tomorrow")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frameworks", default="rules")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--cases", default="", help="comma-separated case ids (subset)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument(
        "--daily",
        action="store_true",
        help="resumable small batch for live LLM frameworks (free-tier quota)",
    )
    ap.add_argument("--max-runs", type=int, default=5, help="runs per --daily batch")
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.daily:
        daily(args)
    elif not args.report_only:
        cases = [c for c in load_cases() if args.split == "all" or c.split == args.split]
        if args.cases:
            wanted = set(args.cases.split(","))
            cases = [c for c in cases if c.id in wanted]
        settings = Settings()
        for fw in args.frameworks.split(","):
            started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            results = asyncio.run(run_all(cases, [fw], args.trials, args.concurrency))
            meta = {
                "framework": fw,
                "dataset_version": dataset_version(),
                "cases": len(cases),
                "split": args.split,
                "trials": args.trials,
                "scorer_version": SCORER_VERSION,
                "model": settings.llm_model if fw != "rules" else "none",
                "temperature": settings.llm_temperature,
                "git_sha": git_sha(),
                "started": started,
            }
            out = RESULTS / f"{started}-{fw}.json"
            out.write_text(
                json.dumps({"meta": meta, "results": results}, indent=1),
                encoding="utf-8",
                newline="\n",
            )
            ok = sum(r["success"] for r in results)
            print(f"{fw}: {ok}/{len(results)} successful runs -> {out.name}")
    md = write_report(latest_per_framework(), REPO_ROOT / "docs" / "benchmarks" / "REPORT.md")
    print(md.split("## Success by category")[0])


if __name__ == "__main__":
    main()
