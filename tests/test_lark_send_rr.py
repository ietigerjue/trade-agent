"""Regression tests for Lark delivery of fixed-RR report summaries."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from send_a_share_report_to_lark import send_lark_cli  # noqa: E402


class LarkSendFixedRRTests(unittest.TestCase):
    def test_file_mode_sends_text_summary_before_markdown_file(self):
        message = "每日A股裸K做多观察报告\n\n【固定5%止损 RR · 最优做多候选】\nRR=4.00 下一压力=12.00"
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
            report_path = Path(tmp_dir) / "a_share_latest.md"
            report_path.write_text(message, encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(args, **kwargs):
                calls.append(args)

            with patch("send_a_share_report_to_lark.find_lark_cli", return_value="lark-cli"):
                with patch("send_a_share_report_to_lark.subprocess.run", side_effect=fake_run):
                    send_lark_cli(
                        text=message,
                        report_path=report_path,
                        chat_id=None,
                        user_id="ou_test",
                        identity="bot",
                        send_mode="file",
                    )

        self.assertEqual(len(calls), 2)
        self.assertIn("--text", calls[0])
        self.assertIn("固定5%止损 RR", calls[0][calls[0].index("--text") + 1])
        self.assertIn("--file", calls[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
