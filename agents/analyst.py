"""
アナリスト: メトリクスを分析してライターへのフィードバックレポートを生成する
"""
import os
import json
import logging
from collections import defaultdict
from utils import load_json, save_json, log_error, now_jst, STATE_DIR

logger = logging.getLogger("analyst")

MIN_POSTS_TO_ANALYZE = 5  # 分析に必要な最低投稿数


def compute_engagement_score(metrics: dict) -> float:
    """エンゲージメントスコアを計算（viewsを基準に正規化）"""
    views = metrics.get("views", 0)
    likes = metrics.get("likes", 0)
    replies = metrics.get("replies", 0)
    if views == 0:
        return 0.0
    return (likes * 3 + replies * 5) / views * 100


def run():
    logger.info("Analyst started")
    history = load_json(STATE_DIR / "post_history.json")

    # メトリクスが取得済みの投稿のみ対象
    posts_with_metrics = [
        p for p in history if p.get("metrics") and p["metrics"].get("views") is not None
    ]

    if len(posts_with_metrics) < MIN_POSTS_TO_ANALYZE:
        logger.info(f"Not enough data ({len(posts_with_metrics)} posts). Skipping analysis.")
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

    # Claudeにフィードバックテキストを生成させる
    feedback_text = generate_feedback_with_claude(
        pattern_avg, topic_avg, top_patterns, avoid_patterns, top_topics, len(posts_with_metrics)
    )

    report = {
        "generated_at": now_jst(),
        "post_count_analyzed": len(posts_with_metrics),
        "top_patterns": top_patterns,
        "avoid_patterns": avoid_patterns,
        "top_topics": top_topics,
        "pattern_scores": {k: round(v, 3) for k, v in pattern_avg.items()},
        "topic_scores": {k: round(v, 3) for k, v in topic_avg.items()},
        "feedback_text": feedback_text,
    }

    save_json(STATE_DIR / "analyst_report.json", report)
    logger.info(f"Analyst done. Top patterns: {top_patterns}, Top topics: {top_topics}")


def generate_feedback_with_claude(pattern_avg, topic_avg, top_patterns, avoid_patterns, top_topics, total) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    summary = f"""直近{total}件の投稿分析結果:

パターン別エンゲージメント（高い順）:
{json.dumps({k: round(v, 3) for k, v in sorted(pattern_avg.items(), key=lambda x: x[1], reverse=True)}, ensure_ascii=False)}

テーマ別エンゲージメント（高い順）:
{json.dumps({k: round(v, 3) for k, v in sorted(topic_avg.items(), key=lambda x: x[1], reverse=True)}, ensure_ascii=False)}
"""

    prompt = f"""{summary}

上記の分析結果をもとに、次のバッチで投稿を生成するライターへの具体的な指示を日本語で書いてください。
- どのパターンを多く使うべきか
- どのテーマを優先すべきか
- 避けるべきパターン・テーマは何か
- その他気づいた改善点

200字程度で具体的に書いてください。"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude feedback generation failed: {e}")
        return f"上位パターン: {top_patterns}。上位テーマ: {top_topics}。避けるパターン: {avoid_patterns}。"


if __name__ == "__main__":
    run()
