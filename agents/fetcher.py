"""
フェッチャー: 投稿から24時間後にThreads APIからメトリクスを取得する
"""
import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from utils import load_json, save_json, log_error, now_jst, STATE_DIR

logger = logging.getLogger("fetcher")

JST = timezone(timedelta(hours=9))
THREADS_API_BASE = "https://graph.threads.net/v1.0"
FETCH_AFTER_HOURS = 24   # 投稿から何時間後に取得するか
FETCH_BEFORE_HOURS = 48  # 何時間後まで取得対象か（それ以降はスキップ）


def get_access_token() -> str:
    return os.getenv("THREADS_ACCESS_TOKEN", "")


def fetch_metrics(threads_id: str) -> dict | None:
    """Threads API Insightsからメトリクスを取得"""
    token = get_access_token()
    url = f"{THREADS_API_BASE}/{threads_id}/insights"
    params = {
        "metric": "views,likes,replies,reposts,quotes,shares",
        "access_token": token,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if not resp.ok:
            logger.error(f"Failed to fetch metrics for {threads_id}: {resp.status_code} body={resp.text}")
            return None
        data = resp.json().get("data", [])
        metrics = {}
        for item in data:
            name = item.get("name")
            value = item.get("values", [{}])[0].get("value", 0) if item.get("values") else item.get("total_value", {}).get("value", 0)
            metrics[name] = value
        return {
            "views": metrics.get("views", 0),
            "likes": metrics.get("likes", 0),
            "replies": metrics.get("replies", 0),
            "reposts": metrics.get("reposts", 0),
            "quotes": metrics.get("quotes", 0),
        }
    except Exception as e:
        logger.error(f"Failed to fetch metrics for {threads_id}: {e}")
        return None


def fetch_replies(threads_id: str, limit: int = 10) -> list[dict]:
    """Threads APIからトップレベル返信を取得する"""
    token = get_access_token()
    url = f"{THREADS_API_BASE}/{threads_id}/replies"
    params = {
        "fields": "id,text,timestamp,username,has_replies,is_reply,root_post,replied_to",
        "reverse": "false",
        "limit": limit,
        "access_token": token,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if not resp.ok:
            logger.error(f"Failed to fetch replies for {threads_id}: {resp.status_code} body={resp.text}")
            return []
        data = resp.json().get("data", [])
        replies = []
        for item in data[:limit]:
            replies.append({
                "id": item.get("id", ""),
                "text": item.get("text", ""),
                "timestamp": item.get("timestamp", ""),
                "username": item.get("username", ""),
                "is_reply": item.get("is_reply", False),
            })
        return replies
    except Exception as e:
        logger.error(f"Failed to fetch replies for {threads_id}: {e}")
        return []


def run(force: bool = False):
    logger.info(f"Fetcher started{'  (force mode)' if force else ''}")
    history = load_json(STATE_DIR / "post_history.json")
    now = datetime.now(JST)
    updated_posts = 0
    metrics_fetch_count = 0
    reply_fetch_count = 0

    for post in history:
        metrics_missing = post.get("metrics") is None or force
        replies_missing = post.get("reply_insights") is None or force

        # 既にメトリクス・返信の両方がある投稿は対象外
        if not metrics_missing and not replies_missing:
            continue

        threads_id = post.get("threads_id", "")
        if not threads_id or threads_id.startswith("DRY_RUN"):
            continue

        ts = post.get("timestamp")
        if not ts:
            continue

        try:
            post_time = datetime.fromisoformat(ts)
            if post_time.tzinfo is None:
                post_time = post_time.replace(tzinfo=JST)
        except ValueError:
            continue

        hours_elapsed = (now - post_time).total_seconds() / 3600

        if not force and hours_elapsed < FETCH_AFTER_HOURS:
            logger.debug(f"Post {post['id']}: too early ({hours_elapsed:.1f}h < {FETCH_AFTER_HOURS}h)")
            continue
        if not force and hours_elapsed > FETCH_BEFORE_HOURS:
            # 古すぎる投稿はとりあえず空データで埋める
            if metrics_missing:
                post["metrics"] = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0, "shares": 0, "note": "fetch_skipped_too_old"}
            if replies_missing:
                post["reply_insights"] = {"sample_replies": [], "note": "fetch_skipped_too_old", "fetched_at": now_jst()}
            continue

        if metrics_missing:
            metrics = fetch_metrics(threads_id)
            if metrics:
                post["metrics"] = metrics
                post["metrics"]["fetched_at"] = now_jst()
                metrics_fetch_count += 1
                logger.info(f"Fetched metrics for {post['id']}: {metrics}")
            else:
                log_error("fetcher", f"Failed to fetch metrics for post {post['id']}", threads_id)

        if replies_missing:
            replies = fetch_replies(threads_id)
            if replies:
                post["reply_insights"] = {
                    "sample_replies": replies,
                    "sample_count": len(replies),
                    "fetched_at": now_jst(),
                }
                reply_fetch_count += 1
                logger.info(f"Fetched replies for {post['id']}: {len(replies)} replies")
            else:
                logger.debug(f"No replies fetched for {post['id']} or fetch failed")

        if metrics_missing or replies_missing:
            updated_posts += 1

    save_json(STATE_DIR / "post_history.json", history)
    logger.info(
        f"Fetcher done. Updated: {updated_posts} posts "
        f"(metrics={metrics_fetch_count}, replies={reply_fetch_count})."
    )


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    run(force=force)
