#!/usr/bin/env python3
"""Send the combined multi-market daily report to Feishu/Lark.

Thin wrapper around send_a_share_report_to_lark.py — changes only the
default report path, title, and idempotency key prefix.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

# Reuse the A-share sender logic
from send_a_share_report_to_lark import (
    read_report,
    send_webhook,
    signed_webhook_payload,
    find_lark_cli,
    send_lark_cli,
)

DEFAULT_REPORT = Path(
    "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/combined/combined_latest.md"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send combined daily report to Feishu/Lark"
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Path to report file",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=7000,
        help="Max chars to send as text (default: 7000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent without actually sending",
    )
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"Report not found: {report_path}", file=sys.stderr)
        return 1

    text = read_report(report_path, args.max_chars)
    today = dt.datetime.now().strftime("%Y-%m-%d")

    # ── Webhook mode ────────────────────────────────────────────────
    webhook_url = os.getenv("LARK_WEBHOOK_URL") or os.getenv("FEISHU_WEBHOOK_URL")
    if webhook_url:
        if args.dry_run:
            print(f"[dry-run] Would send {len(text)} chars to webhook")
            return 0
        webhook_secret = os.getenv("LARK_WEBHOOK_SECRET") or os.getenv("FEISHU_WEBHOOK_SECRET")
        send_webhook(text, webhook_url, webhook_secret)
        print(f"Sent combined report to webhook ({len(text)} chars)")
        return 0

    # ── lark-cli mode ───────────────────────────────────────────────
    lark_cli = find_lark_cli()
    if not lark_cli:
        print(
            "Neither LARK_WEBHOOK_URL nor lark-cli found. "
            "Set a webhook URL or install lark-cli.",
            file=sys.stderr,
        )
        return 1

    chat_id = os.getenv("LARK_CHAT_ID") or os.getenv("FEISHU_CHAT_ID")
    user_id = os.getenv("LARK_USER_ID") or os.getenv("FEISHU_USER_ID")
    if not chat_id and not user_id:
        print(
            "missing Feishu/Lark config: set LARK_WEBHOOK_URL/FEISHU_WEBHOOK_URL "
            "or set LARK_CHAT_ID/LARK_USER_ID with lark-cli",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(f"[dry-run] Would send via lark-cli to chat={chat_id}, user={user_id}")
        return 0

    # Send text summary first
    summary = (
        f"多市场日报 {today}\n\n"
        f"{text[:500]}...\n\n"
        f"[完整报告见文件]"
    )
    # Use file mode for the full report
    identity = os.getenv("LARK_SEND_AS", "bot")
    send_mode = os.getenv("COMBINED_LARK_SEND_MODE", "file")
    send_lark_cli(summary, report_path, chat_id, user_id, identity, send_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
