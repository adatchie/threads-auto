#!/usr/bin/env python3
"""投稿を却下するスクリプト: python scripts/reject.py <post_id>"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from utils import load_json, save_json, STATE_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reject")


def reject_post(post_id: str):
    queue = load_json(STATE_DIR / "post_queue.json")
    found = False
    for item in queue:
        if item["id"] == post_id and item["status"] == "pending":
            item["status"] = "rejected"
            found = True
            break
    if found:
        save_json(STATE_DIR / "post_queue.json", queue)
        logger.info(f"却下しました: {post_id}")
    else:
        logger.error(f"該当なし（IDが違うか既に投稿済み）: {post_id}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("使い方: python scripts/reject.py <post_id>")
        sys.exit(1)
    reject_post(sys.argv[1])
