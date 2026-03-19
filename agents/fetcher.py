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
        "metric": "views,likes,replies,reposts,quotes",
        "access_token": token,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
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


def run():
    logger.info("Fetcher started")
    history = load_json(STATE_DIR / "post_history.json")
    now = datetime.now(JST)
    fetched_count = 0

    for post in history:
        # メトリクス未取得かつDRY_RUN IDでないものが対象
        if post.get("metrics") is not None:
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

        if hours_elapsed < FETCH_AFTER_HOURS:
            logger.debug(f"Post {post['id']}: too early ({hours_elapsed:.1f}h < {FETCH_AFTER_HOURS}h)")
            continue
        if hours_elapsed > FETCH_BEFORE_HOURS:
            # 古すぎる投稿はとりあえず空メトリクスで埋める
            post["metrics"] = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0, "note": "fetch_skipped_too_old"}
            continue

        metrics = fetch_metrics(threads_id)
        if metrics:
            post["metrics"] = metrics
            post["metrics"]["fetched_at"] = now_jst()
            fetched_count += 1
            logger.info(f"Fetched metrics for {post['id']}: {metrics}")
        else:
            log_error("fetcher", f"Failed to fetch metrics for post {post['id']}", threads_id)

    save_json(STATE_DIR / "post_history.json", history)
    logger.info(f"Fetcher done. Fetched: {fetched_count} posts.")


if __name__ == "__main__":
    run()
