$ErrorActionPreference = "Stop"

if ($env:A_SHARE_FORCE_REPORT_ON_CLOSED_MARKET -ne "1") {
    $bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    $calendarPython = $null

    if (Test-Path $bundledPython) {
        $calendarPython = $bundledPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $calendarPython = $pythonCommand.Source
        } else {
            $pyCommand = Get-Command py -ErrorAction SilentlyContinue
            if ($pyCommand) {
                $calendarPython = $pyCommand.Source
            }
        }
    }

    if (-not $calendarPython) {
        throw "No Python runtime found. Install Python or run this from Codex with the bundled runtime available."
    }

    & $calendarPython .\a_share_trading_calendar.py
    $calendarExitCode = $LASTEXITCODE
    if ($calendarExitCode -eq 2) {
        Write-Host "A-share market is closed today. Skipping report generation and Feishu/Lark delivery."
        exit 0
    }
    if ($calendarExitCode -ne 0) {
        exit $calendarExitCode
    }
}

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
