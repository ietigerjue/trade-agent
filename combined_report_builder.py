#!/usr/bin/env python3
"""Combined multi-market daily report builder.

Reads the A-share and US daily reports and merges them into a single
combined report with separate sections, date labels, timezone notes,
and market-open/closed status.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# ── Default report paths ────────────────────────────────────────────────

DEFAULT_A_SHARE_REPORT = Path(
    "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/a-share/daily/a_share_latest.md"
)
DEFAULT_US_REPORT = Path(
    "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/us/daily/us_latest.md"
)
DEFAULT_COMBINED_DIR = Path(
    "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/combined"
)


def _extract_body(markdown: str) -> str:
    """Remove the top-level H1 title line if present, returning body only."""
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# ") and not lines[0].startswith("## "):
        if len(lines) > 1 and lines[1] == "":
            return "\n".join(lines[2:])
        return "\n".join(lines[1:])
    return markdown


def build_combined_report(
    a_share_report_path: Path,
    us_report_path: Path,
    a_share_date: str,
    us_date: str,
    a_share_open: bool = True,
    a_share_reason: str = "",
    us_open: bool = True,
    us_reason: str = "",
    a_share_candidates: int = 0,
    us_candidates: int = 0,
    us_trend: int = 0,
    us_range: int = 0,
) -> str:
    """Assemble the combined A-share + US markdown report."""
    now = dt.datetime.now()
    generated = now.strftime("%Y-%m-%d %H:%M HKT")

    # Read A-share report
    a_body = ""
    if a_share_report_path.is_file():
        a_body = _extract_body(a_share_report_path.read_text(encoding="utf-8"))

    # Read US report
    us_body = ""
    if us_report_path.is_file():
        us_body = _extract_body(us_report_path.read_text(encoding="utf-8"))

    lines: list[str] = []
    lines.append("# 每日多市场裸K做多观察报告")
    lines.append("")
    lines.append(f"Generated: {generated}")
    lines.append("")
    lines.append(
        "这是技术形态和公开行情研究，不是投资建议，也不是自动交易信号。"
    )
    lines.append("")

    # ── Market overview ──────────────────────────────────────────────
    lines.append("## 市场概览")
    lines.append("")

    # A-share status
    a_status = "🟢 开市" if a_share_open else f"🔴 休市 — {a_share_reason}"
    # US status
    us_status_str = "🟢 开市" if us_open else f"🔴 休市 — {us_reason}"

    lines.append(f"- **A股** ({a_share_date}): {a_status}")
    lines.append(f"- **美股** ({us_date} ET): {us_status_str}")
    lines.append("")

    lines.append(
        "| 市场 | 交易日期 | 状态 | 做多候选 | 趋势候选 | 震荡候选 |"
    )
    lines.append("|---|---|---|---|---|---|")
    a_count = f"{a_share_candidates}" if a_share_open else "休市"
    lines.append(
        f"| A股 | {a_share_date} | {a_status} | {a_count} | — | — |"
    )
    us_c = f"{us_candidates}" if us_open else "休市"
    us_t = f"{us_trend}" if us_open else "—"
    us_r = f"{us_range}" if us_open else "—"
    us_s = us_status_str
    lines.append(
        f"| 美股 | {us_date} ET | {us_s} | {us_c} | {us_t} | {us_r} |"
    )
    lines.append("")
    lines.append(
        f"> 美股收盘于美东时间 16:00 = 北京时间次日 04:00。"
        f"本报告美股部分分析的是美东时间 **{us_date}** 的收盘数据。"
    )
    lines.append("")

    # ── A-share section ──────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    if a_share_open and a_body:
        lines.append(f"# A股部分 ({a_share_date})")
        lines.append("")
        lines.append(a_body)
    elif not a_share_open:
        lines.append(f"# A股部分 ({a_share_date})")
        lines.append("")
        lines.append(f"> 🔴 **A股今日休市**: {a_share_reason}")
        lines.append("")
    else:
        lines.append(f"# A股部分 ({a_share_date})")
        lines.append("")
        lines.append("*A股报告未生成*")
    lines.append("")

    # ── US section ───────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    if us_open and us_body:
        lines.append(us_body)
    elif not us_open:
        lines.append(f"# 美股部分 ({us_date} ET)")
        lines.append("")
        lines.append(f"> 🔴 **美股昨日休市**: {us_reason}")
        lines.append("")
    else:
        lines.append(f"# 美股部分 ({us_date} ET)")
        lines.append("")
        lines.append("*美股报告未生成*")
    lines.append("")

    return "\n".join(lines)


def write_combined_report(report: str, report_dir: Path) -> Path:
    """Write combined report to the daily directory."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    dated_path = report_dir / f"combined_daily_{stamp}.md"
    latest_path = report_dir / "combined_latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    return dated_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build combined A-share + US daily report"
    )
    parser.add_argument("--a-share-report", default=str(DEFAULT_A_SHARE_REPORT))
    parser.add_argument("--us-report", default=str(DEFAULT_US_REPORT))
    parser.add_argument(
        "--a-share-date",
        default=dt.date.today().strftime("%Y-%m-%d"),
    )
    parser.add_argument(
        "--us-date",
        default=(dt.date.today() - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    parser.add_argument("--report-dir", default=str(DEFAULT_COMBINED_DIR))
    parser.add_argument("--a-share-open", type=lambda s: s.lower() != "false", default=True)
    parser.add_argument("--a-share-reason", default="")
    parser.add_argument("--us-open", type=lambda s: s.lower() != "false", default=True)
    parser.add_argument("--us-reason", default="")
    parser.add_argument("--a-share-candidates", type=int, default=0)
    parser.add_argument("--us-candidates", type=int, default=0)
    parser.add_argument("--us-trend", type=int, default=0)
    parser.add_argument("--us-range", type=int, default=0)
    args = parser.parse_args()

    report = build_combined_report(
        a_share_report_path=Path(args.a_share_report),
        us_report_path=Path(args.us_report),
        a_share_date=args.a_share_date,
        us_date=args.us_date,
        a_share_open=args.a_share_open,
        a_share_reason=args.a_share_reason,
        us_open=args.us_open,
        us_reason=args.us_reason,
        a_share_candidates=args.a_share_candidates,
        us_candidates=args.us_candidates,
        us_trend=args.us_trend,
        us_range=args.us_range,
    )

    path = write_combined_report(report, Path(args.report_dir))
    print(f"Combined report written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
