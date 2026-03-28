"""
アナリスト: メトリクスを分析してライターへのフィードバックレポートを生成する
"""
import os
import json
import logging
from collections import defaultdict
from utils import load_json, save_json, log_error, now_jst, STATE_DIR
from feedback_state import get_active_feedback, summarize_operator_feedback

logger = logging.getLogger("analyst")

MIN_POSTS_TO_ANALYZE = 2  # 1件だけだとノイズが大きいので最低2件
FULL_ANALYSIS_POSTS = 3   # 3件あれば通常の分析レポートとして扱う
REVIEW_WINDOW = "11:00〜14:00 JST"
NEXT_GENERATION_AT = "14:00 JST"


def compute_engagement_score(metrics: dict) -> float:
    """エンゲージメントスコアを計算（viewsを基準に正規化）"""
    views = metrics.get("views", 0)
    likes = metrics.get("likes", 0)
    replies = metrics.get("replies", 0)
    if views == 0:
        return 0.0
    return (likes * 3 + replies * 5) / views * 100


def summarize_reply_insights(posts_with_metrics: list, max_posts: int = 5, max_replies: int = 3) -> str:
    """返信の文脈を短く要約して LLM に渡す"""
    reply_blocks = []
    recent_posts = [
        post for post in posts_with_metrics
        if post.get("reply_insights", {}).get("sample_replies")
    ][-max_posts:]

    for post in recent_posts:
        topic = post.get("topic_node", "unknown")
        replies = post.get("reply_insights", {}).get("sample_replies", [])[:max_replies]
        snippets = []
        for reply in replies:
            text = (reply.get("text") or "").strip()
            if not text:
                continue
            snippets.append(text[:80])
        if snippets:
            reply_blocks.append(f"- {topic}: " + " / ".join(snippets))

    return "\n".join(reply_blocks) if reply_blocks else "なし"


def run():
    logger.info("Analyst started")
    history = load_json(STATE_DIR / "post_history.json")
    operator_feedback = get_active_feedback()
    operator_feedback_summary = summarize_operator_feedback(operator_feedback)

    # メトリクスが取得済みの投稿のみ対象
    posts_with_metrics = [
        p for p in history if p.get("metrics") and p["metrics"].get("views") is not None
    ]

    if len(posts_with_metrics) < MIN_POSTS_TO_ANALYZE:
        report = {
            "generated_at": now_jst(),
            "analysis_status": "skipped",
            "analysis_note": f"Not enough data ({len(posts_with_metrics)} posts). Skipping analysis.",
            "post_count_analyzed": len(posts_with_metrics),
            "top_patterns": [],
            "avoid_patterns": [],
            "top_topics": [],
            "pattern_scores": {},
            "topic_scores": {},
            "reply_summary": "なし",
            "operator_feedback_count": len(operator_feedback),
            "operator_feedback_summary": operator_feedback_summary,
            "feedback_text": "分析スキップ: まだ十分な投稿データがありません。",
            "review_window": REVIEW_WINDOW,
            "next_generation_at": NEXT_GENERATION_AT,
        }
        save_json(STATE_DIR / "analyst_report.json", report)
        try:
            from discord_notify import send_analysis_report
            send_analysis_report(report)
        except Exception as e:
            logger.warning(f"Discord analysis notification failed: {e}")
        logger.info(report["analysis_note"])
        return

    # パターン別・テーマ別のエンゲージメント集計
    pattern_scores = defaultdict(list)
    topic_scores = defaultdict(list)

    for post in posts_with_metrics:
        score = compute_engagement_score(post["metrics"])
        pattern = post.get("pattern", "unknown")
        topic = post.get("topic_node", "unknown")
        pattern_scores[pattern].append(score)
        topic_scores[topic].append(score)

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    pattern_avg = {p: avg(s) for p, s in pattern_scores.items()}
    topic_avg = {t: avg(s) for t, s in topic_scores.items()}

    sorted_patterns = sorted(pattern_avg.items(), key=lambda x: x[1], reverse=True)
    sorted_topics = sorted(topic_avg.items(), key=lambda x: x[1], reverse=True)

    top_patterns = [p for p, _ in sorted_patterns[:3]]
    avoid_patterns = [p for p, s in sorted_patterns if s < 0.5][:3]
    top_topics = [t for t, _ in sorted_topics[:3]]
    reply_summary = summarize_reply_insights(posts_with_metrics)

    # Claudeにフィードバックテキストを生成させる
    feedback_text = generate_feedback_with_claude(
        pattern_avg,
        topic_avg,
        top_patterns,
        avoid_patterns,
        top_topics,
        len(posts_with_metrics),
        reply_summary,
        operator_feedback_summary,
    )

    analysis_status = "partial" if len(posts_with_metrics) < FULL_ANALYSIS_POSTS else "ok"
    analysis_note = (
        f"サンプル少なめ ({len(posts_with_metrics)}件)。傾向は参考値として扱ってください。"
        if analysis_status == "partial"
        else "十分な件数の投稿を分析しました。"
    )

    report = {
        "generated_at": now_jst(),
        "analysis_status": analysis_status,
        "analysis_note": analysis_note,
        "post_count_analyzed": len(posts_with_metrics),
        "top_patterns": top_patterns,
        "avoid_patterns": avoid_patterns,
        "top_topics": top_topics,
        "pattern_scores": {k: round(v, 3) for k, v in pattern_avg.items()},
        "topic_scores": {k: round(v, 3) for k, v in topic_avg.items()},
        "reply_summary": reply_summary,
        "operator_feedback_count": len(operator_feedback),
        "operator_feedback_summary": operator_feedback_summary,
        "feedback_text": feedback_text,
        "review_window": REVIEW_WINDOW,
        "next_generation_at": NEXT_GENERATION_AT,
    }

    save_json(STATE_DIR / "analyst_report.json", report)
    try:
        from discord_notify import send_analysis_report
        send_analysis_report(report)
    except Exception as e:
        logger.warning(f"Discord analysis notification failed: {e}")
    logger.info(f"Analyst done. Top patterns: {top_patterns}, Top topics: {top_topics}")


def generate_feedback_with_claude(pattern_avg, topic_avg, top_patterns, avoid_patterns, top_topics, total, reply_summary, operator_feedback_summary) -> str:
    from llm import call_llm

    summary = f"""直近{total}件の投稿分析結果:

パターン別エンゲージメント（高い順）:
{json.dumps({k: round(v, 3) for k, v in sorted(pattern_avg.items(), key=lambda x: x[1], reverse=True)}, ensure_ascii=False)}

テーマ別エンゲージメント（高い順）:
{json.dumps({k: round(v, 3) for k, v in sorted(topic_avg.items(), key=lambda x: x[1], reverse=True)}, ensure_ascii=False)}

読者コメントの抜粋:
{reply_summary}

運用者フィードバック:
{operator_feedback_summary}
"""

    prompt = f"""{summary}

上記の分析結果をもとに、次のバッチで投稿を生成するライターへの具体的な指示を日本語で書いてください。
- どのパターンを多く使うべきか
- どのテーマを優先すべきか
- 避けるべきパターン・テーマは何か
- その他気づいた改善点

200字程度で具体的に書いてください。"""

    try:
        return call_llm(prompt, max_tokens=512)
    except Exception as e:
        logger.error(f"LLM feedback generation failed: {e}")
        return f"上位パターン: {top_patterns}。上位テーマ: {top_topics}。避けるパターン: {avoid_patterns}。"


if __name__ == "__main__":
    run()
