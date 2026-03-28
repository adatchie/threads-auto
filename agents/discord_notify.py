"""Discord通知: 生成された投稿プレビューをDiscordに送信する"""
import os
import logging
import requests
from datetime import datetime, timedelta, timezone

from utils import load_json, save_json, now_jst, STATE_DIR

logger = logging.getLogger("discord_notify")
JST = timezone(timedelta(hours=9))


def _post_message(message: str) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL が未設定です。Discord通知をスキップします。")
        return False

    resp = requests.post(webhook_url, json={"content": message}, timeout=10)
    if resp.status_code not in (200, 204):
        logger.error(f"Discord通知失敗: {resp.status_code} {resp.text}")
        return False
    return True


def send_post_preview(post_item: dict) -> bool:
    review_until = datetime.fromisoformat(post_item["review_until"])
    review_str = review_until.strftime("%H:%M")
    content = post_item["content"]
    post_id = post_item["id"]
    score = post_item.get("quality_score", 0)
    pattern = post_item.get("pattern", "")
    topic = post_item.get("topic_node", "unknown")
    category = post_item.get("category", "") or post_item.get("category_id", "")
    affiliate_state = "あり" if post_item.get("has_affiliate") else "なし"
    scheduled_slot = post_item.get("scheduled_slot") or "未設定"

    message = (
        f"📝 **生成プレビュー**\n"
        f"ID: `{post_id}`\n"
        f"テーマ: `{topic}`\n"
        f"カテゴリ: `{category}`\n"
        f"品質スコア: **{score}** | パターン: `{pattern}`\n"
        f"アフィリエイト: `{affiliate_state}`\n"
        f"レビュー期限: **{review_str} JST**\n"
        f"投稿枠: `{scheduled_slot}`\n\n"
        f"**本文**\n"
        f"```\n{content}\n```\n"
        f"**操作**\n"
        f"却下する場合は以下を実行:\n"
        f"```\npython scripts/reject.py {post_id} --reason \"理由\"\n```"
    )

    delivered = _post_message(message)
    _record_delivery("post_preview", delivered, f"{post_id} | {topic} | {pattern}")
    if not delivered:
        return False
    logger.info(f"Discord通知送信完了: {post_id}")
    return True


def send_posted_notification(post_item: dict) -> bool:
    content = post_item["content"]
    preview = content[:120] + ("..." if len(content) > 120 else "")
    message = f"✅ **投稿完了**\n```\n{preview}\n```"
    delivered = _post_message(message)
    _record_delivery("posted_notification", delivered, preview)
    return delivered


def send_error_alert(agent: str, error: str) -> bool:
    message = f"🚨 **エラー発生** [{agent}]\n```\n{error}\n```"
    delivered = _post_message(message)
    _record_delivery("error_alert", delivered, f"{agent}: {error}")
    return delivered


def _shorten(value: str, limit: int = 600) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _record_delivery(kind: str, delivered: bool, summary: str) -> None:
    path = STATE_DIR / "discord_delivery.json"
    data = load_json(path) if path.exists() else {}
    data[kind] = {
        "delivered": bool(delivered),
        "timestamp": now_jst(),
        "summary": _shorten(summary, 500),
    }
    save_json(path, data)


def send_analysis_report(report: dict) -> bool:
    status = report.get("analysis_status", "ok")
    analyzed = report.get("post_count_analyzed", 0)
    top_patterns = ", ".join(report.get("top_patterns", [])[:3]) or "なし"
    avoid_patterns = ", ".join(report.get("avoid_patterns", [])[:3]) or "なし"
    top_topics = ", ".join(report.get("top_topics", [])[:3]) or "なし"
    reply_summary = _shorten(report.get("reply_summary", "なし"), 500)
    operator_summary = _shorten(report.get("operator_feedback_summary", "なし"), 500)
    feedback_text = _shorten(report.get("feedback_text", "なし"), 800)
    analysis_note = _shorten(report.get("analysis_note", "なし"), 500)
    review_window = report.get("review_window", "11:00〜14:00 JST")
    next_generation_at = report.get("next_generation_at", "14:00 JST")

    status_label = {
        "ok": "✅ 通常分析",
        "partial": "⚠️ サンプル少なめ",
        "skipped": "⏭️ 分析スキップ",
    }.get(status, f"ℹ️ {status}")

    summary_block = (
        f"📌 **要約**\n"
        f"- 分析対象: **{analyzed}件**\n"
        f"- 強いパターン: `{top_patterns}`\n"
        f"- 強いテーマ: `{top_topics}`\n"
        f"- 避けるパターン: `{avoid_patterns}`\n"
        f"- 次回生成: `{next_generation_at}`\n"
        f"- レビュー時間: `{review_window}`\n"
    )

    if status == "skipped":
        message = (
            f"📊 **分析レポート** {status_label}\n"
            f"{summary_block}\n"
            f"**状態メモ**\n"
            f"```\n{operator_summary}\n```\n\n"
            f"**分析メモ**\n"
            f"```\n{analysis_note}\n```"
        )
        delivered = _post_message(message)
        _record_delivery("analysis_report", delivered, f"{status_label} | {analysis_note}")
        return delivered

    message = (
        f"📊 **分析レポート** {status_label}\n"
        f"{summary_block}\n"
        f"```\n{analysis_note}\n```\n\n"
        f"**読者コメント抜粋**\n"
        f"```\n{reply_summary}\n```\n\n"
        f"**運用メモ**\n"
        f"```\n{operator_summary}\n```\n\n"
        f"**次回向けフィードバック**\n"
        f"```\n{feedback_text}\n```\n\n"
        f"必要なら方針修正を state に戻してください。\n"
        f"例: `python scripts/operator_feedback.py --mode avoid --scope writing --topics \"...\" --patterns \"...\" --note \"...\" --active-days 7`"
    )
    delivered = _post_message(message)
    _record_delivery("analysis_report", delivered, f"{status_label} | {analysis_note}")
    return delivered


def send_operator_feedback_notice(entry: dict) -> bool:
    scope = str(entry.get("scope", "overall")).strip() or "overall"
    mode = str(entry.get("mode", "note")).strip() or "note"
    topics = ", ".join(entry.get("topics", [])) or "なし"
    patterns = ", ".join(entry.get("patterns", [])) or "なし"
    note = _shorten(entry.get("note", "なし"), 800)
    expires_at = entry.get("expires_at", "なし")

    message = (
        f"🧭 **運用フィードバックを保存しました**\n"
        f"scope: `{scope}` | mode: `{mode}` | expires: `{expires_at}`\n"
        f"topics: `{topics}`\n"
        f"patterns: `{patterns}`\n\n"
        f"```\n{note}\n```"
    )
    return _post_message(message)
