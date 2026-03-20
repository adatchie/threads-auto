"""
リサーチャー: テーマツリーを見て不足ノードを特定し、YouTube/Web/Xからネタを収集する
"""
import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from collections import Counter
from utils import load_json, save_json, log_error, now_jst, STATE_DIR, KNOWLEDGE_DIR

logger = logging.getLogger("researcher")

BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY")
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


def find_underrepresented_nodes(topic_tree: dict, counts: Counter) -> list:
    """カバレッジが低いノードを優先度順に返す"""
    nodes = []
    for cat in topic_tree["categories"]:
        for node in cat["nodes"]:
            nid = node["id"]
            nodes.append({
                "id": nid,
                "name": node["name"],
                "keywords": node["keywords"],
                "category": cat["name"],
                "count": counts.get(nid, 0),
                "priority": node.get("priority", 99),
            })
    # 出現回数が少ない順、同じなら優先度順
    return sorted(nodes, key=lambda n: (n["count"], n["priority"]))


def brave_search(query: str, count: int = 5) -> list[dict]:
    """Brave Search APIでウェブ検索"""
    if not BRAVE_API_KEY:
        logger.warning("BRAVE_SEARCH_API_KEY not set")
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": query, "count": count}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [{"title": r["title"], "url": r["url"], "description": r.get("description", "")} for r in results]
    except Exception as e:
        logger.error(f"Brave search failed: {e}")
        return []


def youtube_search(query: str, max_results: int = 3) -> list[dict]:
    """YouTube Data APIで動画を検索し、文字起こし取得を試みる"""
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


def summarize_with_claude(content: str, topic_name: str, keywords: list[str]) -> list:
    """Anthropicでネタの要点を抽出"""
    import anthropic, json
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
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
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"Anthropic summarize failed: {e}")
        return []

def run(max_nodes: int = 3):
    logger.info("Researcher started")
    history = load_json(STATE_DIR / "post_history.json")
    topic_tree = load_json(KNOWLEDGE_DIR / "topic_tree.json")
    pool = load_json(STATE_DIR / "research_pool.json")

    counts = get_recent_topic_counts(history)
    target_nodes = find_underrepresented_nodes(topic_tree, counts)[:max_nodes]

    new_items = []
    for node in target_nodes:
        logger.info(f"Researching node: {node['name']} (recent count: {node['count']})")
        collected_text = ""

        # Web検索
        for kw in node["keywords"][:2]:
            results = brave_search(kw)
            for r in results:
                collected_text += f"\n{r['title']}\n{r['description']}\n"

        # YouTube検索
        for kw in node["keywords"][:1]:
            yt_results = youtube_search(kw)
            for r in yt_results:
                collected_text += f"\n{r['title']}\n{r['transcript']}\n"

        if not collected_text.strip():
            logger.warning(f"No content found for node: {node['name']}")
            continue

        points = summarize_with_claude(collected_text, node["name"], node["keywords"])
        if not points:
            continue

        for point in points:
            new_items.append({
                "id": f"research_{node['id']}_{now_jst()[:10]}_{len(new_items)}",
                "topic_node": node["id"],
                "topic_name": node["name"],
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
