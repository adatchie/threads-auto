#!/usr/bin/env python3
"""運用フィードバックを state に追加する"""
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))

from feedback_state import load_operator_feedback, save_operator_feedback, parse_csv_list
from utils import now_jst

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("operator_feedback")

JST = timezone(timedelta(hours=9))


def add_operator_feedback(note: str, mode: str, scope: str, topics: str, patterns: str, active_days: int) -> dict:
    entries = load_operator_feedback()
    expires_at = None
    if active_days and active_days > 0:
        expires_at = (datetime.now(JST) + timedelta(days=active_days)).isoformat()

    entry = {
        "id": f"operator_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "created_at": now_jst(),
        "mode": mode,
        "scope": scope,
        "topics": parse_csv_list(topics),
        "patterns": parse_csv_list(patterns),
        "note": note.strip(),
        "active_days": active_days,
        "expires_at": expires_at,
        "source": "manual",
    }
    entries.append(entry)
    save_operator_feedback(entries)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--note", required=True, help="運用メモ")
    parser.add_argument("--mode", default="note", choices=["note", "boost", "avoid", "pause"], help="フィードバック種別")
    parser.add_argument("--scope", default="overall", choices=["overall", "research", "writing", "posting"], help="適用範囲")
    parser.add_argument("--topics", default="", help="対象トピック（カンマ区切り）")
    parser.add_argument("--patterns", default="", help="対象パターン（カンマ区切り）")
    parser.add_argument("--active-days", type=int, default=7, help="有効日数")
    args = parser.parse_args()

    entry = add_operator_feedback(
        note=args.note,
        mode=args.mode,
        scope=args.scope,
        topics=args.topics,
        patterns=args.patterns,
        active_days=args.active_days,
    )

    logger.info(
        "Saved operator feedback: id=%s mode=%s scope=%s topics=%s patterns=%s",
        entry["id"],
        entry["mode"],
        entry["scope"],
        ",".join(entry["topics"]) or "-",
        ",".join(entry["patterns"]) or "-",
    )

    try:
        from discord_notify import send_operator_feedback_notice
        send_operator_feedback_notice(entry)
    except Exception as e:
        logger.warning(f"Discord notification failed: {e}")


if __name__ == "__main__":
    main()
