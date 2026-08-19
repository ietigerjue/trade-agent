#!/usr/bin/env python3
"""
Send the latest A-share report to Feishu/Lark.

Supported configuration:
- LARK_WEBHOOK_URL or FEISHU_WEBHOOK_URL for a custom bot webhook.
- LARK_CHAT_ID or LARK_USER_ID with lark-cli available on PATH.
- If no chat/user env var is set, lark-cli auth status is used to find the
  authorized user's open_id and send a direct-message file.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


DEFAULT_REPORT = Path(
    "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/a-share/daily/a_share_latest.md"
)
DEFAULT_SEND_MODE = "file"


def read_report(path: Path, max_chars: int) -> str:
    content = path.read_text(encoding="utf-8").strip()
    if len(content) <= max_chars:
        return content
    return (
        content[:max_chars].rstrip()
        + "\n\n...报告过长，已截断；完整内容见本机 "
        + "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/a-share/daily/a_share_latest.md"
    )


def title_from_report(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            today = dt.datetime.now().strftime("%Y-%m-%d")
            return f"{line[2:].strip()} {today}"
    return f"A股日报 {dt.datetime.now():%Y-%m-%d}"


def report_date_for_key(report_path: Path, text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Generated:"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    for part in report_path.stem.split("_"):
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            return part
    return dt.datetime.now().strftime("%Y-%m-%d")


def signed_webhook_payload(text: str, secret: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
        sign = base64.b64encode(hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    return payload


def send_webhook(text: str, webhook_url: str, secret: str | None) -> None:
    payload = signed_webhook_payload(text, secret)
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8", errors="ignore")
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        result = {"raw": body}
    if result.get("code") not in (None, 0) and result.get("StatusCode") not in (None, 0):
        raise RuntimeError(f"webhook send failed: {body}")


def find_lark_cli() -> str | None:
    for name in ("lark-cli", "lark-cli.cmd", "lark-cli.ps1"):
        executable = shutil.which(name)
        if executable:
            return executable
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"
    for name in ("lark-cli.cmd", "lark-cli.ps1", "lark-cli"):
        candidate = npm_bin / name
        try:
            exists = candidate.exists()
        except OSError:
            exists = False
        if exists:
            return str(candidate)
    return None


def resolve_lark_user_id(executable: str) -> str | None:
    try:
        completed = subprocess.run(
            [executable, "auth", "status"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        status = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    user_id = status.get("userOpenId")
    if isinstance(user_id, str) and user_id:
        return user_id
    user = status.get("identities", {}).get("user", {})
    open_id = user.get("openId")
    return open_id if isinstance(open_id, str) and open_id else None


def build_lark_cli_target_args(executable: str, chat_id: str | None, user_id: str | None) -> list[str]:
    if chat_id:
        return ["--chat-id", chat_id]
    if user_id:
        return ["--user-id", user_id]
    resolved_user_id = resolve_lark_user_id(executable)
    if resolved_user_id:
        return ["--user-id", resolved_user_id]
    raise RuntimeError("set LARK_CHAT_ID or LARK_USER_ID, or run lark-cli auth login")


def report_arg_for_lark_cli(report_path: Path) -> tuple[str, str | None]:
    """Return (file_arg, cwd_override) for lark-cli --file.

    lark-cli requires a relative path for --file. When the report is under CWD,
    return the relative path with no cwd override. Otherwise return just the
    filename and the report's parent directory so the caller can cd there.
    """
    absolute_report = report_path.resolve()
    cwd = Path.cwd().resolve()
    try:
        return str(absolute_report.relative_to(cwd)), None
    except ValueError:
        return absolute_report.name, str(absolute_report.parent)


def send_lark_cli(
    text: str,
    report_path: Path,
    chat_id: str | None,
    user_id: str | None,
    identity: str,
    send_mode: str,
) -> None:
    executable = find_lark_cli()
    if not executable:
        raise RuntimeError("lark-cli is not on PATH")
    target_args = build_lark_cli_target_args(executable, chat_id, user_id)
    base_args = [executable, "im", "+messages-send", "--as", identity]
    base_args.extend(target_args)
    run_kwargs: dict[str, Any] = {}
    if send_mode == "file":
        summary_args = [*base_args, "--text", text]
        summary_args.extend(["--idempotency-key", f"a-share-daily-{report_date_for_key(report_path, text)}-file-summary"])
        subprocess.run(summary_args, check=True, text=True)

        file_arg, file_cwd = report_arg_for_lark_cli(report_path)
        args = [*base_args]
        args.extend(["--file", file_arg])
        if file_cwd is not None:
            run_kwargs["cwd"] = file_cwd
    else:
        args = [*base_args]
        args.extend(["--text", text])
    args.extend(["--idempotency-key", f"a-share-daily-{report_date_for_key(report_path, text)}-{send_mode}"])
    subprocess.run(args, check=True, text=True, **run_kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the latest A-share report to Feishu/Lark.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Markdown report path.")
    parser.add_argument("--max-chars", type=int, default=7000, help="Maximum message characters to send.")
    parser.add_argument(
        "--send-mode",
        choices=("file", "text"),
        default=os.environ.get("A_SHARE_LARK_SEND_MODE", DEFAULT_SEND_MODE),
        help="Send as a Markdown file through lark-cli, or as text.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print message instead of sending.")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"report not found: {report_path}", file=sys.stderr)
        return 1

    report = read_report(report_path, args.max_chars)
    message = f"{title_from_report(report)}\n\n{report}"

    if args.dry_run:
        print(message)
        return 0

    chat_id = os.environ.get("LARK_CHAT_ID") or os.environ.get("FEISHU_CHAT_ID")
    user_id = os.environ.get("LARK_USER_ID") or os.environ.get("FEISHU_USER_ID")
    webhook_url = os.environ.get("LARK_WEBHOOK_URL") or os.environ.get("FEISHU_WEBHOOK_URL")
    identity = os.environ.get("LARK_SEND_AS", "bot")
    if not webhook_url and not chat_id and not user_id:
        raise RuntimeError(
            "missing Feishu/Lark config: set LARK_WEBHOOK_URL/FEISHU_WEBHOOK_URL "
            "or set LARK_CHAT_ID/LARK_USER_ID with lark-cli"
        )
    if args.send_mode == "file":
        send_lark_cli(message, report_path, chat_id, user_id, identity, args.send_mode)
        print(f"Sent A-share report via lark-cli {args.send_mode}")
        return 0

    webhook_secret = os.environ.get("LARK_WEBHOOK_SECRET") or os.environ.get("FEISHU_WEBHOOK_SECRET")
    if webhook_url:
        send_webhook(message, webhook_url, webhook_secret)
        print("Sent A-share report via Feishu/Lark webhook")
        return 0

    send_lark_cli(message, report_path, chat_id, user_id, identity, args.send_mode)
    print(f"Sent A-share report via lark-cli {args.send_mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
