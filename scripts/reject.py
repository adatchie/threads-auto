#!/usr/bin/env python3
"""投稿を却下するスクリプト: python scripts/reject.py <post_id>"""
import sys
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from utils import load_json, save_json, STATE_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reject")
JST = timezone(timedelta(hours=9))


def _now_jst() -> str:
    return datetime.now(JST).isoformat()


def _append_review_feedback(entry: dict) -> None:
    feedback_path = STATE_DIR / "review_feedback.json"
    feedback = load_json(feedback_path) if feedback_path.exists() else []
    feedback.append(entry)
    save_json(feedback_path, feedback)


def reject_post(post_id: str, reason: str = ""):
    queue = load_json(STATE_DIR / "post_queue.json")
    found = False
    for item in queue:
        if item["id"] == post_id and item["status"] == "pending":
            item["status"] = "rejected"
            item["rejected_at"] = _now_jst()
            item["rejection_reason"] = reason.strip()
            item["rejected_by"] = "manual_review"
            found = True

            _append_review_feedback({
                "id": f"review_{post_id}",
                "post_id": post_id,
                "pattern": item.get("pattern", ""),
                "topic_node": item.get("topic_node", ""),
                "content_preview": item.get("content", "")[:160],
                "reason": reason.strip(),
                "created_at": _now_jst(),
            })
            break
    if found:
        save_json(STATE_DIR / "post_queue.json", queue)
        if reason.strip():
            logger.info(f"却下しました: {post_id} (reason={reason.strip()})")
        else:
            logger.info(f"却下しました: {post_id}")
    else:
        logger.error(f"該当なし（IDが違うか既に投稿済み）: {post_id}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("post_id", help="却下する投稿のID")
    parser.add_argument("--reason", default="", help="却下理由（任意）")
    args = parser.parse_args()
    reject_post(args.post_id, args.reason)
