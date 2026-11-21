# Daily live-evaluation batch (run by the "WorkflowOps daily eval" scheduled task).
# Runs the next few cases within the Gemini free-tier quota, then rebuilds the report.
# Output is appended to logs\daily-eval.log (git-ignored). Nothing is committed or pushed.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
New-Item -ItemType Directory -Force "logs" | Out-Null
$log = Join-Path $repo "logs\daily-eval.log"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts\uv.exe" }

"==== $(Get-Date -Format s) ====" | Out-File -Append -Encoding utf8 $log
& $uv run python scripts/run_eval.py --daily --frameworks gemini,adk,langgraph --max-runs 5 *>&1 |
    Out-File -Append -Encoding utf8 $log
& $uv run python scripts/run_eval.py --report-only *>&1 | Select-Object -First 3 |
    Out-File -Append -Encoding utf8 $log
