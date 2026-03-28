"""
ポスター: キューから投稿をThreads APIで実行する（cronから呼ばれる）
"""
import os
import logging
import time
import random
import requests
from datetime import datetime, timedelta, timezone
from utils import load_json, save_json, log_error, now_jst, is_kill_switch_on, STATE_DIR, KNOWLEDGE_DIR
from feedback_state import get_active_feedback, derive_operator_targets

logger = logging.getLogger("poster")

JST = timezone(timedelta(hours=9))
THREADS_API_BASE = "https://graph.threads.net/v1.0"
MAX_POSTS_PER_DAY = 3
MIN_INTERVAL_MINUTES = 60
RANDOM_DELAY_SECONDS = 300  # ±5分

# 1日のタイムスロット（24h表記 JST）- ピーク時間帯3本に絞る
# 朝出勤前・昼休み・夜帰宅後
TIME_SLOTS = [8, 12, 20]  # 3スロット


def get_access_token() -> str:
    return os.getenv("THREADS_ACCESS_TOKEN", "")


def get_user_id() -> str:
    return os.getenv("THREADS_USER_ID", "")


def get_headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}"}


def post_to_threads(content: str) -> str | None:
    """Threads APIに投稿して投稿IDを返す"""
    user_id = get_user_id()
    token = get_access_token()

    # Step 1: メディアコンテナ作成
    create_url = f"{THREADS_API_BASE}/{user_id}/threads"
    resp = requests.post(create_url, data={
        "media_type": "TEXT",
        "text": content,
        "access_token": token,
    }, timeout=15)

    if resp.status_code != 200:
        raise Exception(f"Container creation failed: {resp.status_code} {resp.text}")

    container_id = resp.json().get("id")
    if not container_id:
        raise Exception(f"No container ID in response: {resp.text}")

    # Step 2: 少し待ってから公開
    time.sleep(3)

    # Step 3: 公開
    publish_url = f"{THREADS_API_BASE}/{user_id}/threads_publish"
    resp2 = requests.post(publish_url, data={
        "creation_id": container_id,
        "access_token": token,
    }, timeout=15)

    if resp2.status_code != 200:
        raise Exception(f"Publish failed: {resp2.status_code} {resp2.text}")

    threads_id = resp2.json().get("id")
    return threads_id


def reply_to_threads(threads_id: str, content: str) -> str | None:
    """投稿への返信（コメント欄投稿）"""
    user_id = get_user_id()
    token = get_access_token()

    create_url = f"{THREADS_API_BASE}/{user_id}/threads"
    resp = requests.post(create_url, data={
        "media_type": "TEXT",
        "text": content,
        "reply_to_id": threads_id,
        "access_token": token,
    }, timeout=15)

    if resp.status_code != 200:
        raise Exception(f"Reply container failed: {resp.status_code} {resp.text}")

    container_id = resp.json().get("id")
    time.sleep(3)

    publish_url = f"{THREADS_API_BASE}/{user_id}/threads_publish"
    resp2 = requests.post(publish_url, data={
        "creation_id": container_id,
        "access_token": token,
    }, timeout=15)

    if resp2.status_code != 200:
        raise Exception(f"Reply publish failed: {resp2.status_code} {resp2.text}")

    return resp2.json().get("id")


def count_today_posts(history: list) -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    return sum(1 for p in history if p.get("timestamp", "").startswith(today))


def get_last_post_time(history: list) -> datetime | None:
    posted = [p for p in history if p.get("timestamp")]
    if not posted:
        return None
    last = max(posted, key=lambda p: p["timestamp"])
    ts = datetime.fromisoformat(last["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=JST)
    return ts


def should_post_now() -> bool:
    """現在時刻が投稿スロットに近いか確認"""
    now_hour = datetime.now(JST).hour
    return now_hour in TIME_SLOTS


def format_time_slots() -> str:
    return ", ".join(f"{slot:02d}:00" for slot in TIME_SLOTS)



def is_review_period_over(post_item: dict) -> bool:
    """review_until を過ぎているか確認（過ぎていたら投稿OK）"""
    from datetime import datetime, timedelta, timezone
    JST = timezone(timedelta(hours=9))
    review_until_str = post_item.get("review_until")
    if not review_until_str:
        return True
    try:
        review_until = datetime.fromisoformat(review_until_str)
        if review_until.tzinfo is None:
            review_until = review_until.replace(tzinfo=JST)
        return datetime.now(JST) >= review_until
    except Exception:
        return True


def is_scheduled_slot_reached(post_item: dict) -> bool:
    """scheduled_slot を過ぎているか確認（過ぎていたら投稿OK）"""
    from datetime import datetime, timedelta, timezone
    JST = timezone(timedelta(hours=9))
    slot_str = post_item.get("scheduled_slot")
    if not slot_str:
        return True
    try:
        slot = datetime.fromisoformat(slot_str)
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=JST)
        return datetime.now(JST) >= slot
    except Exception:
        return True

def run(dry_run: bool = False):
    if is_kill_switch_on():
        logger.warning("KILL_SWITCH is enabled. Poster aborted.")
        return

    logger.info(f"Poster started (dry_run={dry_run})")

    posting_feedback = get_active_feedback("posting")
    posting_targets = derive_operator_targets(posting_feedback)
    if posting_targets["pause_generation"]:
        logger.info("Operator feedback requested a posting pause. Poster aborted.")
        return

    if not should_post_now():
        logger.info(f"Outside posting slots ({format_time_slots()} JST).")
        if not dry_run:
            return
        logger.info("[DRY RUN] Continuing simulation despite slot gate.")

    history = load_json(STATE_DIR / "post_history.json")
    queue = load_json(STATE_DIR / "post_queue.json")
    affiliate = load_json(KNOWLEDGE_DIR / "affiliate.json")

    # 1日の投稿上限チェック
    today_count = count_today_posts(history)
    if today_count >= MAX_POSTS_PER_DAY:
        logger.info(f"Daily limit reached ({today_count}/{MAX_POSTS_PER_DAY}). Exiting.")
        return

    # 最低間隔チェック
    last_time = get_last_post_time(history)
    if last_time:
        elapsed = (datetime.now(JST) - last_time).total_seconds() / 60
        if elapsed < MIN_INTERVAL_MINUTES:
            logger.info(f"Too soon since last post ({elapsed:.0f} min < {MIN_INTERVAL_MINUTES} min). Exiting.")
            return

    # キューから次の投稿を取得
    pending = [
        q for q in queue
        if q["status"] == "pending"
        and is_review_period_over(q)
        and is_scheduled_slot_reached(q)
    ]
    if not pending:
        waiting = [q for q in queue if q["status"] == "pending"]
        if waiting:
            nxt = min(waiting, key=lambda q: q.get("scheduled_slot") or q.get("review_until", ""))
            logger.info(f"スロット待機中。次の投稿可能: {nxt.get('scheduled_slot') or nxt.get('review_until', '')}")
    if not pending:
        logger.info("No pending posts in queue.")
        return

    post_item = pending[0]

    # ランダム遅延（bot検出回避）
    delay = random.randint(0, RANDOM_DELAY_SECONDS)
    logger.info(f"Waiting {delay}s before posting (human-like delay)...")
    if not dry_run:
        time.sleep(delay)

    content = post_item["content"]

    if dry_run:
        logger.info(f"[DRY RUN] Would post: {content[:100]}...")
        threads_id = "DRY_RUN_ID"
    else:
        try:
            threads_id = post_to_threads(content)
            logger.info(f"Posted successfully: threads_id={threads_id}")
        except Exception as e:
            log_error("poster", "Post failed", str(e))
            logger.error(f"Post failed: {e}")
            return

    # アフィリエイトコメント投稿
    affiliate_comment_id = None
    if post_item.get("has_affiliate") and post_item.get("affiliate_campaign_id"):
        camp_id = post_item["affiliate_campaign_id"]
        campaign = next((c for c in affiliate["campaigns"] if c["id"] == camp_id), None)
        if campaign:
            comment_text = campaign["comment_text"]
            if dry_run:
                logger.info(f"[DRY RUN] Would post affiliate comment: {comment_text[:80]}...")
                affiliate_comment_id = "DRY_RUN_COMMENT_ID"
            else:
                try:
                    time.sleep(5)  # 本文投稿後少し待つ
                    affiliate_comment_id = reply_to_threads(threads_id, comment_text)
                    logger.info(f"Affiliate comment posted: {affiliate_comment_id}")
                except Exception as e:
                    log_error("poster", "Affiliate comment failed", str(e))
                    logger.warning(f"Affiliate comment failed: {e}")

    if dry_run:
        logger.info(
            f"[DRY RUN] No state changes were made for {post_item['id']} "
            f"(threads_id={threads_id}, affiliate_comment_id={affiliate_comment_id})"
        )
        return

    # 投稿履歴に追加
    history_item = {
        "id": post_item["id"],
        "timestamp": now_jst(),
        "threads_id": threads_id,
        "content": content,
        "pattern": post_item["pattern"],
        "topic_node": post_item["topic_node"],
        "quality_score": post_item["quality_score"],
        "has_affiliate": post_item.get("has_affiliate", False),
        "affiliate_comment_id": affiliate_comment_id,
        "metrics": None,
    }
    history.append(history_item)
    save_json(STATE_DIR / "post_history.json", history)

    # キューのステータス更新
    for item in queue:
        if item["id"] == post_item["id"]:
            item["status"] = "posted"
            item["posted_at"] = now_jst()
            item["threads_id"] = threads_id
            break
    save_json(STATE_DIR / "post_queue.json", queue)


    # Discord投稿完了通知
    try:
        from discord_notify import send_posted_notification
        send_posted_notification(post_item)
    except Exception as e:
        logger.warning(f"Discord完了通知失敗: {e}")
    logger.info(f"Poster done. Posted: {post_item['id']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
