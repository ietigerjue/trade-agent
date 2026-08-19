# run_daily_scheduled.ps1 — Windows Task Scheduler entry point
# Scheduled at 14:30 HKT Mon-Fri for combined A-share + US stock daily report.
# No arguments needed; the scheduler just runs this file.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSCommandPath -Parent)

$logDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

$logFile = Join-Path $logDir "scheduled-$(Get-Date -Format 'yyyy-MM-dd-HHmmss').log"
Start-Transcript -Path $logFile -Append

try {
    & "$PSScriptRoot\run_combined_daily_and_send_lark.ps1"
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Combined daily report completed successfully."
} catch {
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] FAILED: $_"
    throw
} finally {
    Stop-Transcript
}
