"""
リサーチャー: テーマツリーを見て不足ノードを特定し、Web検索からネタを収集・要約する
"""
import os
import json
import logging
import time
import requests
from datetime import datetime, timedelta, timezone
from collections import Counter
from utils import load_json, save_json, log_error, now_jst, STATE_DIR, KNOWLEDGE_DIR, is_kill_switch_on
from feedback_state import get_active_feedback, derive_operator_targets, topic_tokens
from search import web_search
from llm import call_llm_json

logger = logging.getLogger("researcher")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
JST = timezone(timedelta(hours=9))


def get_recent_topic_counts(history: list, days: int = 14) -> Counter:
    """直近N日間の各ノードの出現回数を集計"""
    cutoff = datetime.now(JST) - timedelta(days=days)
    counts = Counter()
    for post in history:
        ts = post.get("timestamp")
        if not ts:
            continue
        try:
            post_time = datetime.fromisoformat(ts)
            if post_time.tzinfo is None:
                post_time = post_time.replace(tzinfo=JST)
            if post_time >= cutoff:
                counts[post.get("topic_node", "")] += 1
        except ValueError:
            pass
    return counts


def find_underrepresented_nodes(topic_tree: dict, counts: Counter, boost_targets: set[str] | None = None, avoid_targets: set[str] | None = None) -> list:
    """カバレッジが低いノードを優先度順に返す"""
    boost_targets = set(boost_targets or [])
    avoid_targets = set(avoid_targets or [])
    nodes = []
    for cat in topic_tree["categories"]:
        for node in cat["nodes"]:
            nid = node["id"]
            nodes.append({
                "id": nid,
                "name": node["name"],
                "keywords": node["keywords"],
                "category_id": cat["id"],
                "category": cat["name"],
                "count": counts.get(nid, 0),
                "priority": node.get("priority", 99),
            })

    def sort_key(node: dict) -> tuple[int, int, int]:
        tokens = topic_tokens(node)
        if tokens & boost_targets:
            rank = 0
        elif tokens & avoid_targets:
            rank = 2
        else:
            rank = 1
        return (rank, node["count"], node["priority"])

    return sorted(nodes, key=sort_key)


def youtube_search(query: str, max_results: int = 3) -> list[dict]:
    """YouTube Data APIで動画を検索"""
    if not YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY not set, skipping YouTube search")
        return []
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key": YOUTUBE_API_KEY,
        "q": query,
        "part": "snippet",
        "type": "video",
        "maxResults": max_results,
        "relevanceLanguage": "ja",
        "regionCode": "JP",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        results = []
        for item in items:
            vid = item["id"]["videoId"]
            title = item["snippet"]["title"]
            transcript = _get_transcript(vid)
            results.append({"video_id": vid, "title": title, "transcript": transcript[:2000] if transcript else ""})
        return results
    except Exception as e:
        logger.error(f"YouTube search failed: {e}")
        return []


def _get_transcript(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=["ja"])
        return " ".join(s["text"] for s in segments)
    except Exception:
        return ""


def summarize_content(content: str, topic_name: str, keywords: list[str]) -> list:
    """LLMでネタの要点を抽出"""
    prompt = f"""以下のコンテンツから、Threads投稿のネタとして使えるポイントを3〜5個抽出してください。
テーマ: {topic_name}（キーワード: {', '.join(keywords)}）
コンテンツ:
{content[:3000]}
出力形式（JSON配列のみ）:
[
  {{"point": "ネタのポイント", "detail": "詳細や具体例", "hook_potential": "高/中/低"}},
  ...
]"""
    try:
        return call_llm_json(prompt, max_tokens=1024)
    except Exception as e:
        logger.error(f"LLM summarize failed: {e}")
        return []


def run(max_nodes: int = 3):
    logger.info("Researcher started")
    if is_kill_switch_on():
        logger.warning("KILL_SWITCH is enabled. Researcher aborted.")
        return

    history = load_json(STATE_DIR / "post_history.json")
    topic_tree = load_json(KNOWLEDGE_DIR / "topic_tree.json")
    pool = load_json(STATE_DIR / "research_pool.json")
    operator_feedback = get_active_feedback("research")
    operator_targets = derive_operator_targets(operator_feedback)

    counts = get_recent_topic_counts(history)
    target_nodes = find_underrepresented_nodes(
        topic_tree,
        counts,
        operator_targets["boost_topics"],
        operator_targets["avoid_topics"],
    )[:max_nodes]

    new_items = []
    for node in target_nodes:
        logger.info(f"Researching node: {node['name']} (recent count: {node['count']})")
        collected_text = ""

        # Web検索（バックエンドは search.py が自動選択）
        for kw in node["keywords"][:2]:
            try:
                results = web_search(kw)
                for r in results:
                    collected_text += f"\n{r['title']}\n{r['description']}\n"
                if results:
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Search failed for '{kw}': {e}")

        # YouTube検索
        for kw in node["keywords"][:1]:
            yt_results = youtube_search(kw)
            for r in yt_results:
                collected_text += f"\n{r['title']}\n{r['transcript']}\n"

        if not collected_text.strip():
            logger.warning(f"No content found for node: {node['name']}")
            continue

        points = summarize_content(collected_text, node["name"], node["keywords"])
        if not points:
            continue

        for point in points:
            new_items.append({
                "id": f"research_{node['id']}_{now_jst()[:10]}_{len(new_items)}",
                "topic_node": node["id"],
                "topic_name": node["name"],
                "category_id": node["category_id"],
                "category": node["category"],
                "point": point.get("point", ""),
                "detail": point.get("detail", ""),
                "hook_potential": point.get("hook_potential", "中"),
                "collected_at": now_jst(),
                "used": False,
            })

    pool.extend(new_items)
    save_json(STATE_DIR / "research_pool.json", pool)
    logger.info(f"Researcher done. Added {len(new_items)} items to research pool.")


if __name__ == "__main__":
    run()
