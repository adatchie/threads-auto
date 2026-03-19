"""
ライター: リサーチネタ＋アナリストフィードバックをもとに投稿を生成・採点・キューへ追加する
"""
import os
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from utils import load_json, save_json, log_error, now_jst, STATE_DIR, KNOWLEDGE_DIR

logger = logging.getLogger("writer")

JST = timezone(timedelta(hours=9))
MAX_RETRIES = 2
MIN_QUALITY_SCORE = 7.0
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


def select_pattern(patterns: list, recent_patterns: list, top_patterns: list) -> dict:
    """パターンを選択: 直近3件と被らず、アナリスト推奨を優先"""
    # 使えるパターンを絞り込み
    available = [p for p in patterns if p["id"] not in recent_patterns]
    if not available:
        available = patterns  # 全部被ってたらリセット

    # アナリスト推奨パターンがあれば優先
    preferred = [p for p in available if p["id"] in top_patterns]
    if preferred:
        import random
        return random.choice(preferred)

    import random
    return random.choice(available)


def select_topic_node(pool: list, recent_topics: list) -> dict | None:
    """リサーチプールから未使用のネタを選ぶ（テーマ連続を避ける）"""
    unused = [item for item in pool if not item.get("used")]
    if not unused:
        return None

    # 直近テーマと被らないものを優先
    non_repeat = [item for item in unused if item.get("topic_node") not in recent_topics]
    candidates = non_repeat if non_repeat else unused

    # hook_potential: 高 > 中 > 低
    priority_map = {"高": 0, "中": 1, "低": 2}
    candidates.sort(key=lambda x: priority_map.get(x.get("hook_potential", "中"), 1))
    return candidates[0]


def generate_post(pattern: dict, topic: dict, account: dict, analyst_report: dict, hooks: list) -> str:
    """Claudeで投稿を生成"""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def score_post(content: str, pattern: dict, account: dict) -> float:
    """Claudeで投稿を10項目採点し、平均スコアを返す"""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        result = json.loads(text)
        return float(result.get("average", 0))
    except Exception as e:
        logger.error(f"Scoring failed: {e}")
        return 0.0


def run(count: int = 10):
    logger.info(f"Writer started. Target: {count} posts")
    history = load_json(STATE_DIR / "post_history.json")
    queue = load_json(STATE_DIR / "post_queue.json")
    pool = load_json(STATE_DIR / "research_pool.json")
    patterns = load_json(KNOWLEDGE_DIR / "post_patterns.json")["patterns"]
    hooks = load_json(KNOWLEDGE_DIR / "hook_lines.json")["hooks"]
    account = load_json(KNOWLEDGE_DIR / "account.json")
    analyst_report = load_json(STATE_DIR / "analyst_report.json")
    affiliate = load_json(KNOWLEDGE_DIR / "affiliate.json")

    recent_patterns = get_recent_used_patterns(history)
    recent_topics = get_recent_used_topics(history)
    top_patterns = analyst_report.get("top_patterns", [])

    generated = 0
    rejected = 0

    for _ in range(count * 3):  # 上限を設けてループ
        if generated >= count:
            break

        topic = select_topic_node(pool, recent_topics)
        if not topic:
            logger.warning("Research pool exhausted")
            break

        pattern = select_pattern(patterns, recent_patterns, top_patterns)

        # アフィリエイト判定
        affiliate_campaigns = affiliate.get("campaigns", [])
        affiliate_campaign = None
        for camp in affiliate_campaigns:
            if topic["topic_node"] in camp.get("trigger_topics", []):
                # 本日のアフィ投稿数チェック
                today = datetime.now(JST).strftime("%Y-%m-%d")
                today_affiliate_count = sum(
                    1 for p in history
                    if p.get("has_affiliate") and p.get("timestamp", "").startswith(today)
                )
                max_aff = affiliate.get("rules", {}).get("max_affiliate_posts_per_day", 3)
                if today_affiliate_count < max_aff:
                    import random
                    if random.random() < camp.get("frequency", 0.3):
                        affiliate_campaign = camp
                break

        # 投稿生成（最大MAX_RETRIESまで再生成）
        content = None
        score = 0.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                candidate = generate_post(pattern, topic, account, analyst_report, hooks)
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
                log_error("writer", f"Post generation failed", str(e))
                break

        if content is None:
            rejected += 1
            continue

        # キューに追加
        post_id = f"post_{uuid.uuid4().hex[:8]}"
        queue_item = {
            "id": post_id,
            "content": content,
            "pattern": pattern["id"],
            "topic_node": topic["topic_node"],
            "quality_score": round(score, 2),
            "has_affiliate": affiliate_campaign is not None,
            "affiliate_campaign_id": affiliate_campaign["id"] if affiliate_campaign else None,
            "status": "pending",
            "created_at": now_jst(),
            "scheduled_slot": None,  # Posterが設定
        }
        queue.append(queue_item)

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
    logger.info(f"Writer done. Generated: {generated}, Rejected: {rejected}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    run(args.count)
