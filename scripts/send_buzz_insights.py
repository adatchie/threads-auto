#!/usr/bin/env python3
"""buzz_insights.json を Discord に要約送信する"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = BASE_DIR / "agents"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from utils import load_json, STATE_DIR  # noqa: E402
from discord_notify import send_buzz_insights_report  # noqa: E402


def main() -> int:
    path = STATE_DIR / "buzz_insights.json"
    if not path.exists():
        print(f"buzz insights file not found: {path}")
        return 1

    report = load_json(path)
    if not isinstance(report, dict):
        print("buzz insights report is not a JSON object")
        return 1

    ok = send_buzz_insights_report(report)
    if ok:
        print("buzz insights report sent to Discord")
        return 0

    print("failed to send buzz insights report to Discord")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
