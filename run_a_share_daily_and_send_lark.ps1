$ErrorActionPreference = "Stop"

.\run_a_share_daily_agent.ps1 @args

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (Test-Path $bundledPython) {
    & $bundledPython .\send_a_share_report_to_lark.py
    exit $LASTEXITCODE
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    & $pythonCommand.Source .\send_a_share_report_to_lark.py
    exit $LASTEXITCODE
}

$pyCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pyCommand) {
    & $pyCommand.Source .\send_a_share_report_to_lark.py
    exit $LASTEXITCODE
}

throw "No Python runtime found. Install Python or run this from Codex with the bundled runtime available."
