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

from backend.core.config import REPO_ROOT, Settings
from backend.evaluation.cases import dataset_version, load_cases
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frameworks", default="rules")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--cases", default="", help="comma-separated case ids (subset)")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not args.report_only:
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
