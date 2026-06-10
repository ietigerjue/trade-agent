$ErrorActionPreference = "Stop"

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (Test-Path $bundledPython) {
    & $bundledPython .\review_a_share_10day_signals.py @args
    exit $LASTEXITCODE
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    & $pythonCommand.Source .\review_a_share_10day_signals.py @args
    exit $LASTEXITCODE
}

$pyCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pyCommand) {
    & $pyCommand.Source .\review_a_share_10day_signals.py @args
    exit $LASTEXITCODE
}

throw "No Python runtime found. Install Python or run this from Codex with the bundled runtime available."
