# run_combined_daily_and_send_lark.ps1
# Combined A-share + US stock daily report orchestrator.
# Runs EVERY trading day at 14:30 HKT. Trading calendars decide whether to skip.
# Closed markets are noted in the report rather than silently skipped.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSCommandPath -Parent)

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

function Find-Python {
    if (Test-Path $bundledPython) { return $bundledPython }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "No Python runtime found."
}

$python = Find-Python
$aShareDate = (Get-Date).ToString("yyyy-MM-dd")
$usDate = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
$reportRoot = "F:\VibeCoding\Codex和ClaudeCode\Memory Base\03_Skill产物\trade-agent\reports"

function Write-TextFile {
    param(
        [string]$Path,
        [string]$Content
    )
    $dir = Split-Path $Path -Parent
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
    $Content | Set-Content -Path $Path -Encoding utf8
}

function Write-AShareClosedReport {
    param(
        [string]$Date,
        [string]$Reason
    )
    $dir = Join-Path $reportRoot "a-share\daily"
    $datedPath = Join-Path $dir "a_share_daily_$Date.md"
    $latestPath = Join-Path $dir "a_share_latest.md"
    $content = @"
# A股每日裸K观察报告（$Date）

> 市场状态：休市 — $Reason

## 大盘环境
A股 $Date 休市，未运行行情扫描。

## 快速结论
- 今日无严格入场触发候选；原因：市场休市。
- 30日速度、60日涨幅、60日买卖压力是趋势质量字段，不是严格入场条件。

## 做多候选
*今日休市，无严格入场触发候选。*

## 趋势良好观察池
*今日休市，未生成趋势观察池。*

## 板块T+0基金观察
*今日休市，未生成板块T+0基金观察。*

## 策略二：威科夫再吸筹观察
*今日休市，未生成策略二观察。*

## 自选股策略检查
*今日休市，未运行自选股策略检查。*

## 消息面摘录
*今日休市，未抓取新增消息面。*

## 方法
休市占位报告由组合日报 wrapper 生成，用于保留日报链路和 Feishu/Lark 合并报告上下文。

## 数据备注
- 市场关闭原因：$Reason
"@
    Write-TextFile -Path $datedPath -Content $content
    Write-TextFile -Path $latestPath -Content $content
    Write-Host "A-share closed report written to $datedPath"
}

function Write-USClosedReport {
    param(
        [string]$Date,
        [string]$Reason
    )
    $dir = Join-Path $reportRoot "us\daily"
    $datedPath = Join-Path $dir "us_daily_$Date.md"
    $latestPath = Join-Path $dir "us_latest.md"
    $content = @"
# 每日美股裸K做多观察报告（$Date ET）

> 市场状态：休市 — $Reason

## 快速结论
- 今日无美股做多候选；原因：市场休市。
- 30日速度、60日涨幅、60日买卖压力是趋势质量字段，不是严格入场条件。

## 做多候选
*今日休市，无严格入场触发候选。*

## Al Brooks价格行为观察
*今日休市，未生成价格行为观察。*

## 趋势良好观察池
*今日休市，未生成趋势观察池。*

## 策略二：震荡区间选股观察
*今日休市，未生成震荡区间观察。*

## 数据备注
- 市场关闭原因：$Reason
"@
    Write-TextFile -Path $datedPath -Content $content
    Write-TextFile -Path $latestPath -Content $content
    Write-Host "US closed report written to $datedPath"
}

function Get-AShareTradingStatus {
    param([string]$Date)
    $json = & $python -c "import datetime as d,json,sys,a_share_trading_calendar as c; s=c.a_share_trading_day_status(d.date.fromisoformat(sys.argv[1])); print(json.dumps({'is_open':s.is_open,'reason':s.reason,'source':s.source}, ensure_ascii=True))" $Date
    if ($LASTEXITCODE -ne 0 -or -not $json) {
        throw "A-share trading calendar status check failed for $Date"
    }
    return $json | ConvertFrom-Json
}

function Get-USTradingStatus {
    param([string]$Date)
    $json = & $python -c "import datetime as d,json,sys,us_trading_calendar as c; s=c.us_trading_day_status(d.date.fromisoformat(sys.argv[1])); print(json.dumps({'is_open':s.is_open,'reason':s.reason,'source':s.source}, ensure_ascii=True))" $Date
    if ($LASTEXITCODE -ne 0 -or -not $json) {
        throw "US trading calendar status check failed for $Date"
    }
    return $json | ConvertFrom-Json
}

function Format-ClosedReason {
    param($Status)
    if ($Status.source) {
        return "$($Status.reason) source=$($Status.source)"
    }
    return $Status.reason
}

# Market status checks
$aShareOpen = $true
$aShareReason = "trading-day"
if ($env:COMBINED_FORCE_A_SHARE -ne "1") {
    $status = Get-AShareTradingStatus -Date $aShareDate
    if (-not $status.is_open) {
        $aShareOpen = $false
        $aShareReason = Format-ClosedReason -Status $status
    }
}

$usOpen = $true
$usReason = "trading-day"
if ($env:COMBINED_FORCE_US -ne "1") {
    $status = Get-USTradingStatus -Date $usDate
    if (-not $status.is_open) {
        $usOpen = $false
        $usReason = Format-ClosedReason -Status $status
    }
}

# Generate reports
$aShareCandidates = 0
$usCandidates = 0
$usTrend = 0
$usRange = 0

if ($aShareOpen) {
    Write-Host "A-share market OPEN ($aShareDate). Generating report..."
    & $python .\a_share_daily_agent.py --top 8 --workers 24
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: A-share agent exited with code $LASTEXITCODE"
    } else {
        Write-Host "A-share report done."
    }
} else {
    Write-Host "A-share market CLOSED ($aShareDate): $aShareReason"
    Write-AShareClosedReport -Date $aShareDate -Reason $aShareReason
}

if ($usOpen) {
    Write-Host "US market OPEN ($usDate ET). Generating report..."
    $usOutput = & $python .\us_daily_agent.py --top 8 --workers 12 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: US agent exited with code $LASTEXITCODE"
    } else {
        Write-Host "US report done."
        foreach ($line in $usOutput) {
            if ($line -match "Candidates: (\d+) price-action, (\d+) trend, (\d+) range-bound") {
                $usCandidates = [int]$Matches[1]
                $usTrend = [int]$Matches[2]
                $usRange = [int]$Matches[3]
            }
        }
    }
} else {
    Write-Host "US market CLOSED ($usDate ET): $usReason"
    Write-USClosedReport -Date $usDate -Reason $usReason
}

# Build combined report (always, even if both markets closed)
Write-Host "Building combined report..."
& $python .\combined_report_builder.py `
    --a-share-date $aShareDate `
    --us-date $usDate `
    --a-share-candidates $aShareCandidates `
    --us-candidates $usCandidates `
    --us-trend $usTrend `
    --us-range $usRange `
    --a-share-open $aShareOpen `
    --a-share-reason $aShareReason `
    --us-open $usOpen `
    --us-reason $usReason
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: combined_report_builder exited with code $LASTEXITCODE"
}

# Send to Lark
if ($env:COMBINED_SKIP_LARK -ne "1") {
    Write-Host "Sending combined report to Lark..."
    & $python .\send_combined_report_to_lark.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Lark send exited with code $LASTEXITCODE"
    }
}

Write-Host "Combined daily pipeline complete."
