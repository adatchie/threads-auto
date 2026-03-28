"""
ライター: リサーチネタ＋アナリストフィードバックをもとに投稿を生成・採点・キューへ追加する
"""
import os
import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from utils import load_json, save_json, log_error, now_jst, STATE_DIR, KNOWLEDGE_DIR, is_kill_switch_on
from feedback_state import (
    get_active_feedback,
    summarize_operator_feedback,
    derive_operator_targets,
    topic_tokens,
    pattern_tokens,
)

logger = logging.getLogger("writer")

JST = timezone(timedelta(hours=9))
MAX_RETRIES = 2
REVIEW_HOURS = 2  # Discord確認猶予時間（時間）
MIN_QUALITY_SCORE = 6.5
MAX_SIMILARITY = 0.85
RECENT_PATTERN_WINDOW = 3  # 直近N件で同パターン禁止
RECENT_THEME_WINDOW = 3    # 直近N件で同テーマ連続禁止
MAX_HISTORY_FOR_SIMILARITY = 100

SCORE_DIMENSIONS = [
    "フックの強さ（1行目でスクロールを止めるか）",
    "有益性（読者が何かを得られるか）",
    "具体性（数字・事例・固有名詞があるか）",
    "テンポ感（読みやすい改行・リズムか）",
    "ペルソナ一致度（転職を考える20〜35歳に刺さるか）",
    "独自性（ありきたりでないか）",
    "読了率予測（最後まで読まれそうか）",
    "行動喚起力（いいね・コメント・保存を促すか）",
    "差別化（過去の投稿と被っていないか）",
    "Threads最適化（Threadsユーザーの雰囲気に合っているか）",
]


def get_recent_used_patterns(history: list) -> list:
    return [p.get("pattern", "") for p in history[-RECENT_PATTERN_WINDOW:]]


def get_recent_used_topics(history: list) -> list:
    return [p.get("topic_node", "") for p in history[-RECENT_THEME_WINDOW:]]


def compute_similarity(new_text: str, history: list) -> float:
    """過去投稿との最大コサイン類似度を計算"""
    recent = [p.get("content", "") for p in history[-MAX_HISTORY_FOR_SIMILARITY:] if p.get("content")]
    if not recent:
        return 0.0
    try:
        vectorizer = TfidfVectorizer()
        all_texts = recent + [new_text]
        tfidf = vectorizer.fit_transform(all_texts)
        sims = cosine_similarity(tfidf[-1], tfidf[:-1])
        return float(sims.max())
    except Exception:
        return 0.0


def select_pattern(patterns: list, recent_patterns: list, top_patterns: list, avoid_patterns: list | None = None, boost_patterns: list | None = None) -> dict:
    """パターンを選択: 却下・直近重複を避けつつ、アナリスト推奨を優先"""
    avoid_patterns = set(avoid_patterns or [])
    boost_patterns = set(boost_patterns or [])
    recent_patterns = set(recent_patterns)

    available = [
        p for p in patterns
        if p["id"] not in recent_patterns and not (pattern_tokens(p) & avoid_patterns)
    ]
    boosted = [
        p for p in patterns
        if not (pattern_tokens(p) & avoid_patterns) and (pattern_tokens(p) & boost_patterns)
    ]
    if boosted:
        available = [p for p in boosted if p["id"] not in recent_patterns] or boosted
    elif not available:
        available = [p for p in patterns if p["id"] not in recent_patterns] or [p for p in patterns if not (pattern_tokens(p) & avoid_patterns)] or patterns

    # アナリスト推奨パターンがあれば優先
    preferred = [p for p in available if p["id"] in top_patterns]
    if preferred:
        import random
        return random.choice(preferred)

    import random
    return random.choice(available)


def select_topic_node(pool: list, recent_topics: list, avoid_topics: list | None = None, boost_topics: list | None = None) -> dict | None:
    """リサーチプールから未使用のネタを選ぶ（テーマ連続を避ける）"""
    avoid_topics = set(avoid_topics or [])
    boost_topics = set(boost_topics or [])
    unused = [item for item in pool if not item.get("used")]
    if not unused:
        return None

    # 直近テーマと被らないものを優先
    non_repeat = [
        item for item in unused
        if item.get("topic_node") not in recent_topics and not (topic_tokens(item) & avoid_topics)
    ]
    boosted = [
        item for item in unused
        if not (topic_tokens(item) & avoid_topics) and (topic_tokens(item) & boost_topics)
    ]
    if boosted:
        candidates = [item for item in boosted if item.get("topic_node") not in recent_topics] or boosted
    else:
        if not non_repeat:
            non_repeat = [item for item in unused if not (topic_tokens(item) & avoid_topics)]
        candidates = non_repeat if non_repeat else unused

    # hook_potential: 高 > 中 > 低
    priority_map = {"高": 0, "中": 1, "低": 2}
    candidates.sort(key=lambda x: priority_map.get(x.get("hook_potential", "中"), 1))
    return candidates[0]


def build_topic_lookup(topic_tree: dict) -> dict:
    """topic_node からカテゴリID・カテゴリ名を逆引きできるようにする"""
    node_to_category_id = {}
    node_to_category_name = {}
    for category in topic_tree.get("categories", []):
        category_id = category.get("id", "")
        category_name = category.get("name", "")
        for node in category.get("nodes", []):
            node_id = node.get("id", "")
            if not node_id:
                continue
            node_to_category_id[node_id] = category_id
            node_to_category_name[node_id] = category_name
    return {
        "node_to_category_id": node_to_category_id,
        "node_to_category_name": node_to_category_name,
    }


def topic_matches_campaign(topic: dict, campaign: dict, topic_lookup: dict) -> bool:
    """投稿テーマがアフィリエイトの発火条件に合致するかを判定する"""
    trigger_topics = set(campaign.get("trigger_topics", []))
    topic_node = topic.get("topic_node", "")
    topic_category_id = topic.get("category_id") or topic_lookup["node_to_category_id"].get(topic_node, "")
    topic_category_name = topic.get("category") or topic_lookup["node_to_category_name"].get(topic_node, "")

    return (
        topic_node in trigger_topics
        or topic_category_id in trigger_topics
        or topic_category_name in trigger_topics
    )


def summarize_review_feedback(review_feedback: list, limit: int = 5) -> str:
    """直近の却下理由を短く要約してプロンプトに入れる"""
    if not review_feedback:
        return "なし"

    recent = review_feedback[-limit:]
    lines = []
    for item in recent:
        reason = item.get("reason", "").strip() or "manual_reject"
        pattern = item.get("pattern", "unknown")
        topic = item.get("topic_node", "unknown")
        lines.append(f"- {pattern} / {topic}: {reason}")
    return "\n".join(lines)


def derive_feedback_avoid_lists(review_feedback: list) -> tuple[list[str], list[str]]:
    """レビュー履歴から、一時的に避けるべきパターンとテーマを推定する"""
    recent = review_feedback[-10:]
    pattern_counts = Counter(item.get("pattern", "") for item in recent if item.get("pattern"))
    topic_counts = Counter(item.get("topic_node", "") for item in recent if item.get("topic_node"))

    avoid_patterns = [pattern for pattern, count in pattern_counts.items() if count >= 2]
    avoid_topics = [topic for topic, count in topic_counts.items() if count >= 2]
    return avoid_patterns, avoid_topics


def generate_post(pattern: dict, topic: dict, account: dict, analyst_report: dict, hooks: list) -> str:
    """LLMで投稿を生成"""
    from llm import call_llm

    hook_examples = "\n".join([f'- {h["example"]}' for h in hooks[:5]])
    feedback = analyst_report.get("feedback_text", "")

    prompt = f"""あなたはThreadsの転職系インフルエンサーとして投稿を書くライターです。

【アカウント設定】
口調: {account['persona']['tone']}
ターゲット: {account['persona']['target']}

【投稿パターン】
パターン名: {pattern['name']}
説明: {pattern['description']}
構成: {pattern['structure']}

【ネタ情報】
テーマ: {topic['topic_name']}
ポイント: {topic['point']}
詳細: {topic['detail']}

【1行目のフック例（構造を参考に）】
{hook_examples}

【アナリストからのフィードバック】
{feedback}

【ルール】
- 500文字以内
- ハッシュタグなし
- 1〜2行ごとに改行を入れて読みやすくする
- NGワード禁止: {', '.join(account['ng_words'])}
- 1行目が最重要: スクロールを止めるフックにする

投稿本文のみを出力してください（前置き・説明不要）。"""

    return call_llm(prompt, max_tokens=800)


def score_post(content: str, pattern: dict, account: dict) -> float:
    """LLMで投稿を10項目採点し、平均スコアを返す"""
    from llm import call_llm_json

    dimensions_text = "\n".join([f"{i+1}. {d}" for i, d in enumerate(SCORE_DIMENSIONS)])

    prompt = f"""以下のThreads投稿を10項目それぞれ10点満点で採点してください。

【投稿】
{content}

【採点項目】
{dimensions_text}

【採点基準】
- 10点: 非常に優秀
- 7点: 合格ライン
- 5点: 普通
- 3点以下: 改善が必要

JSON形式で出力してください:
{{"scores": [点数, 点数, ...（10個）], "average": 平均値, "feedback": "改善点を一言で"}}"""

    try:
        result = call_llm_json(prompt, max_tokens=512)
        avg = float(result.get("average", 0))
        logger.info(f"  Scoring result: average={avg}, feedback={result.get('feedback', '')[:60]}")
        return avg
    except Exception as e:
        logger.error(f"Scoring failed: {e}")
        # スコアリングのJSON解析失敗時は中間スコアを返す（投稿自体は生成できているので棄却しない）
        logger.warning("Scoring JSON parse failed, using default score 7.0")
        return 7.0


def run(count: int = 10):
    logger.info(f"Writer started. Target: {count} posts")
    if is_kill_switch_on():
        logger.warning("KILL_SWITCH is enabled. Writer aborted.")
        save_json(STATE_DIR / "writer_debug.json", {
            "run_at": now_jst(),
            "generated": 0,
            "rejected": 0,
            "aborted_by_kill_switch": True,
            "debug_log": [],
        })
        return

    history = load_json(STATE_DIR / "post_history.json")
    queue = load_json(STATE_DIR / "post_queue.json")
    pool = load_json(STATE_DIR / "research_pool.json")
    topic_tree = load_json(KNOWLEDGE_DIR / "topic_tree.json")
    patterns = load_json(KNOWLEDGE_DIR / "post_patterns.json")["patterns"]
    hooks = load_json(KNOWLEDGE_DIR / "hook_lines.json")["hooks"]
    account = load_json(KNOWLEDGE_DIR / "account.json")
    analyst_report = load_json(STATE_DIR / "analyst_report.json")
    affiliate = load_json(KNOWLEDGE_DIR / "affiliate.json")
    review_feedback_path = STATE_DIR / "review_feedback.json"
    review_feedback = load_json(review_feedback_path) if review_feedback_path.exists() else []
    operator_feedback = get_active_feedback("writing")
    operator_targets = derive_operator_targets(operator_feedback)
    operator_feedback_summary = summarize_operator_feedback(operator_feedback)
    topic_lookup = build_topic_lookup(topic_tree)

    recent_patterns = get_recent_used_patterns(history)
    recent_topics = get_recent_used_topics(history)
    top_patterns = analyst_report.get("top_patterns", [])
    avoid_patterns = list(analyst_report.get("avoid_patterns", []))
    review_avoid_patterns, review_avoid_topics = derive_feedback_avoid_lists(review_feedback)
    avoid_patterns = list(dict.fromkeys(avoid_patterns + review_avoid_patterns))
    review_feedback_summary = summarize_review_feedback(review_feedback)

    if operator_targets["pause_generation"]:
        logger.info("Operator feedback requested a pause. Writer skipped.")
        save_json(STATE_DIR / "writer_debug.json", {
            "run_at": now_jst(),
            "generated": 0,
            "rejected": 0,
            "paused_by_operator_feedback": True,
            "operator_feedback_count": len(operator_feedback),
            "operator_feedback_summary": operator_feedback_summary,
            "review_feedback_count": len(review_feedback),
            "avoid_patterns_from_review": review_avoid_patterns,
            "avoid_topics_from_review": review_avoid_topics,
            "debug_log": [],
        })
        return

    generated = 0
    rejected = 0
    debug_log = []  # デバッグ情報

    for _ in range(count * 3):  # 上限を設けてループ
        if generated >= count:
            break

        topic = select_topic_node(
            pool,
            recent_topics,
            list(set(review_avoid_topics) | set(operator_targets["avoid_topics"])),
            list(operator_targets["boost_topics"]),
        )
        if not topic:
            logger.warning("Research pool exhausted")
            break

        pattern = select_pattern(
            patterns,
            recent_patterns,
            top_patterns,
            list(set(avoid_patterns) | set(operator_targets["avoid_patterns"])),
            list(operator_targets["boost_patterns"]),
        )

        # アフィリエイト判定
        affiliate_campaigns = affiliate.get("campaigns", [])
        affiliate_campaign = None
        for camp in affiliate_campaigns:
            if not topic_matches_campaign(topic, camp, topic_lookup):
                continue

            # 本日のアフィ投稿数チェック
            today = datetime.now(JST).strftime("%Y-%m-%d")
            today_affiliate_count = sum(
                1 for p in history
                if p.get("has_affiliate") and p.get("timestamp", "").startswith(today)
            )
            max_aff = affiliate.get("rules", {}).get("max_affiliate_posts_per_day", 3)
            if today_affiliate_count >= max_aff:
                break

            import random
            if random.random() < camp.get("frequency", 0.3):
                affiliate_campaign = camp
                break

        # 投稿生成（最大MAX_RETRIESまで再生成）
        content = None
        score = 0.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                prompt_analyst_report = dict(analyst_report)
                prompt_analyst_report["feedback_text"] = (
                    (analyst_report.get("feedback_text", "") or "").strip()
                    + "\n\n【直近の却下フィードバック】\n"
                    + review_feedback_summary
                    + "\n\n【運用者フィードバック】\n"
                    + operator_feedback_summary
                ).strip()

                candidate = generate_post(pattern, topic, account, prompt_analyst_report, hooks)
                from llm import call_llm as _clm
                debug_entry = {"topic": topic["topic_node"], "pattern": pattern["id"], "attempt": attempt+1, "content_len": len(candidate), "content_preview": candidate[:200], "backend": getattr(_clm, '_last_backend', 'unknown')}
                debug_log.append(debug_entry)
                logger.info(f"  Generated content length: {len(candidate)} chars, first 50: {repr(candidate[:50])}")
                if not candidate or len(candidate.strip()) < 20:
                    debug_entry["status"] = "empty_content"
                    logger.warning(f"  Empty or too short content, skipping")
                    continue
                score = score_post(candidate, pattern, account)
                logger.info(f"  Generated post (attempt {attempt+1}): score={score:.1f}")

                if score >= MIN_QUALITY_SCORE:
                    # 類似度チェック
                    sim = compute_similarity(candidate, history)
                    if sim >= MAX_SIMILARITY:
                        logger.info(f"  Rejected: too similar (sim={sim:.2f})")
                        rejected += 1
                        break
                    content = candidate
                    break
                else:
                    logger.info(f"  Score too low ({score:.1f} < {MIN_QUALITY_SCORE}), retrying...")
            except Exception as e:
                debug_log.append({"topic": topic["topic_node"], "pattern": pattern["id"], "attempt": attempt+1, "error": str(e)[:200]})
                log_error("writer", f"Post generation failed", str(e))
                break

        if content is None:
            rejected += 1
            # 失敗したtopicもusedにマークして無限ループを防ぐ
            for item in pool:
                if item["id"] == topic["id"]:
                    item["used"] = True
                    break
            continue

        # キューに追加
        post_id = f"post_{uuid.uuid4().hex[:8]}"
        review_until = (datetime.now(JST) + timedelta(hours=REVIEW_HOURS)).isoformat()
        queue_item = {
            "id": post_id,
            "content": content,
            "pattern": pattern["id"],
            "topic_node": topic["topic_node"],
            "category_id": topic.get("category_id") or topic_lookup["node_to_category_id"].get(topic["topic_node"], ""),
            "category": topic.get("category") or topic_lookup["node_to_category_name"].get(topic["topic_node"], ""),
            "quality_score": round(score, 2),
            "has_affiliate": affiliate_campaign is not None,
            "affiliate_campaign_id": affiliate_campaign["id"] if affiliate_campaign else None,
            "status": "pending",
            "review_until": review_until,
            "created_at": now_jst(),
            "scheduled_slot": None,  # Posterが設定
        }
        queue.append(queue_item)

        # 生成された投稿は Discord に流して手動レビューできるようにする
        try:
            from discord_notify import send_post_preview
            send_post_preview(queue_item)
        except Exception as e:
            logger.warning(f"Discord preview notification failed: {e}")

        # ネタを使用済みにマーク
        for item in pool:
            if item["id"] == topic["id"]:
                item["used"] = True
                break

        recent_patterns = (recent_patterns + [pattern["id"]])[-RECENT_PATTERN_WINDOW:]
        recent_topics = (recent_topics + [topic["topic_node"]])[-RECENT_THEME_WINDOW:]
        generated += 1
        logger.info(f"  Queued: {post_id} (pattern={pattern['id']}, topic={topic['topic_node']}, score={score:.1f})")

    save_json(STATE_DIR / "post_queue.json", queue)
    save_json(STATE_DIR / "research_pool.json", pool)
    save_json(STATE_DIR / "writer_debug.json", {
        "run_at": now_jst(),
        "generated": generated,
        "rejected": rejected,
        "review_feedback_count": len(review_feedback),
        "operator_feedback_count": len(operator_feedback),
        "operator_feedback_summary": operator_feedback_summary,
        "paused_by_operator_feedback": False,
        "avoid_patterns_from_review": review_avoid_patterns,
        "avoid_topics_from_review": review_avoid_topics,
        "avoid_patterns_from_operator": list(operator_targets["avoid_patterns"]),
        "avoid_topics_from_operator": list(operator_targets["avoid_topics"]),
        "boost_patterns_from_operator": list(operator_targets["boost_patterns"]),
        "boost_topics_from_operator": list(operator_targets["boost_topics"]),
        "debug_log": debug_log,
    })

    logger.info(f"Writer done. Generated: {generated}, Rejected: {rejected}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    run(args.count)
