"""Discord通知: 生成された投稿プレビューをDiscordに送信する"""
import os
import logging
import requests
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("discord_notify")
JST = timezone(timedelta(hours=9))


def send_post_preview(post_item: dict) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL が未設定です。Discord通知をスキップします。")
        return False

    review_until = datetime.fromisoformat(post_item["review_until"])
    review_str = review_until.strftime("%H:%M")
    content = post_item["content"]
    post_id = post_item["id"]
    score = post_item.get("quality_score", 0)
    pattern = post_item.get("pattern", "")

    message = (
        f"📝 **新しい投稿が生成されました**\n"
        f"```\n{content}\n```\n"
        f"🎯 品質スコア: **{score}** | パターン: `{pattern}`\n"
        f"⏰ **{review_str} JST** に自動投稿されます\n\n"
        f"❌ 却下する場合は以下を実行:\n"
        f"```\npython scripts/reject.py {post_id}\n```"
    )

    resp = requests.post(webhook_url, json={"content": message}, timeout=10)
    if resp.status_code not in (200, 204):
        logger.error(f"Discord通知失敗: {resp.status_code} {resp.text}")
        return False
    logger.info(f"Discord通知送信完了: {post_id}")
    return True


def send_posted_notification(post_item: dict) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return False
    content = post_item["content"]
    preview = content[:120] + ("..." if len(content) > 120 else "")
    message = f"✅ **投稿完了**\n```\n{preview}\n```"
    resp = requests.post(webhook_url, json={"content": message}, timeout=10)
    return resp.status_code in (200, 204)


def send_error_alert(agent: str, error: str) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return False
    message = f"🚨 **エラー発生** [{agent}]\n```\n{error}\n```"
    resp = requests.post(webhook_url, json={"content": message}, timeout=10)
    return resp.status_code in (200, 204)
