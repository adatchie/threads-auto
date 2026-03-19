"""
スーパーバイザー: 全体監視・異常検知・KILL_SWITCH管理・日次レポート
"""
import logging
from datetime import datetime, timedelta, timezone
from utils import load_json, save_json, log_error, now_jst, STATE_DIR

logger = logging.getLogger("supervisor")

JST = timezone(timedelta(hours=9))
MAX_CONSECUTIVE_ERRORS = 3
EXPECTED_POSTS_PER_DAY = 8  # 最低この数は投稿されるべき
ALERT_IF_BELOW = 5


def check_errors_and_maybe_kill():
    """直近のエラーが連続3回以上なら KILL_SWITCH を有効にする"""
    errors = load_json(STATE_DIR / "error_log.json")
    if len(errors) < MAX_CONSECUTIVE_ERRORS:
        return

    recent = errors[-MAX_CONSECUTIVE_ERRORS:]
    # 連続エラー判定: 直近N件が全て同一セッション的に発生しているか
    now = datetime.now(JST)
    recent_in_window = [
        e for e in recent
        if (now - datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).astimezone(JST)).total_seconds() < 3600
    ]

    if len(recent_in_window) >= MAX_CONSECUTIVE_ERRORS:
        ks = load_json(STATE_DIR / "kill_switch.json")
        if not ks["enabled"]:
            ks["enabled"] = True
            ks["reason"] = f"Auto-triggered: {MAX_CONSECUTIVE_ERRORS} consecutive errors in 1 hour"
            ks["triggered_at"] = now_jst()
            save_json(STATE_DIR / "kill_switch.json", ks)
            logger.critical(f"KILL_SWITCH ACTIVATED: {ks['reason']}")


def check_posting_health(history: list) -> str:
    """今日の投稿数が想定を大きく下回っていないか確認"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    today_posts = [p for p in history if p.get("timestamp", "").startswith(today)]
    hour = datetime.now(JST).hour

    # 夜21時以降に判定
    if hour >= 21:
        if len(today_posts) < ALERT_IF_BELOW:
            msg = f"WARNING: Only {len(today_posts)} posts today (expected >= {ALERT_IF_BELOW})"
            logger.warning(msg)
            return msg
    return f"OK: {len(today_posts)} posts today"


def generate_daily_report(history: list, queue: list) -> str:
    """日次サマリーを生成"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")

    today_posts = [p for p in history if p.get("timestamp", "").startswith(today)]
    yesterday_posts = [p for p in history if p.get("timestamp", "").startswith(yesterday)]

    # 昨日の投稿のメトリクス集計
    yd_views = sum(p.get("metrics", {}).get("views", 0) or 0 for p in yesterday_posts if p.get("metrics"))
    yd_likes = sum(p.get("metrics", {}).get("likes", 0) or 0 for p in yesterday_posts if p.get("metrics"))
    yd_replies = sum(p.get("metrics", {}).get("replies", 0) or 0 for p in yesterday_posts if p.get("metrics"))

    pending_queue = len([q for q in queue if q["status"] == "pending"])
    errors = load_json(STATE_DIR / "error_log.json")
    today_errors = [e for e in errors if e.get("timestamp", "").startswith(today)]

    ks = load_json(STATE_DIR / "kill_switch.json")

    report = f"""
=== 日次レポート {today} ===
今日の投稿数: {len(today_posts)}
キュー残り: {pending_queue}件
今日のエラー: {len(today_errors)}件
KILL_SWITCH: {"有効 ⚠️" if ks["enabled"] else "無効 ✅"}

昨日の成果:
  投稿数: {len(yesterday_posts)}件
  合計インプレッション: {yd_views:,}
  合計いいね: {yd_likes}
  合計リプライ: {yd_replies}
===============================
"""
    return report.strip()


def check(report_only: bool = False):
    logger.info("Supervisor checking...")
    history = load_json(STATE_DIR / "post_history.json")
    queue = load_json(STATE_DIR / "post_queue.json")

    if not report_only:
        check_errors_and_maybe_kill()
        health = check_posting_health(history)
        logger.info(f"Posting health: {health}")

    report = generate_daily_report(history, queue)
    logger.info(report)
    return report


def kill(reason: str = "Manual kill"):
    ks = load_json(STATE_DIR / "kill_switch.json")
    ks["enabled"] = True
    ks["reason"] = reason
    ks["triggered_at"] = now_jst()
    save_json(STATE_DIR / "kill_switch.json", ks)
    logger.critical(f"KILL_SWITCH ACTIVATED: {reason}")


def revive():
    ks = load_json(STATE_DIR / "kill_switch.json")
    ks["enabled"] = False
    ks["reason"] = None
    ks["triggered_at"] = None
    save_json(STATE_DIR / "kill_switch.json", ks)
    logger.info("KILL_SWITCH disabled. System resumed.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--kill", type=str, help="Kill with reason")
    parser.add_argument("--revive", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    if args.kill:
        kill(args.kill)
    elif args.revive:
        revive()
    elif args.report:
        check(report_only=True)
    else:
        check()
