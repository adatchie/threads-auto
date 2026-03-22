"""
リサーチャー: テーマツリーを見て不足ノードを特定し、YouTube/Web/Xからネタを収集する
"""
import os
import logging
import time
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
    """Brave Search APIでウェブ検索。429時はリトライ。"""
    if not BRAVE_API_KEY:
        logger.warning("BRAVE_SEARCH_API_KEY not set")
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": query, "count": count}
    waits = [0, 60, 120]
    for attempt, wait in enumerate(waits):
        if wait:
            logger.info(f"Brave search waiting {wait}s before retry...")
            time.sleep(wait)
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("web", {}).get("results", [])
                return [{"title": r["title"], "url": r["url"], "description": r.get("description", "")} for r in results]
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", waits[min(attempt + 1, len(waits) - 1)]))
                logger.warning(f"Brave search rate limited (attempt {attempt + 1}/{len(waits)}, retry-after={retry_after}s)")
                if attempt < len(waits) - 1:
                    waits[attempt + 1] = max(waits[attempt + 1], retry_after)
            else:
                logger.error(f"Brave search failed: {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"Brave search error: {e}")
            return []
    logger.error(f"Brave search gave up after {len(waits)} retries (429)")
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


def _call_claude(prompt: str, max_tokens: int = 1024) -> str:
    """Claude APIを呼び出してテキストを返す共通ヘルパー"""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _parse_json_points(text: str) -> list:
    import json
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)


def summarize_with_claude(content: str, topic_name: str, keywords: list[str]) -> list:
    """Anthropicでネタの要点を抽出"""
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
        text = _call_claude(prompt)
        return _parse_json_points(text)
    except Exception as e:
        logger.error(f"Anthropic summarize failed: {e}")
        return []


def research_with_claude_only(topic_name: str, keywords: list[str]) -> list:
    """外部検索が使えない場合にClaudeの知識でネタを生成するフォールバック"""
    logger.info(f"Falling back to Claude-only research for: {topic_name}")
    prompt = f"""あなたは日本のキャリア・転職領域に詳しいコンテンツライターです。
外部検索なしに、あなたの知識から以下のテーマのThreads投稿ネタを5個生成してください。

テーマ: {topic_name}
キーワード: {', '.join(keywords)}

条件:
- 日本のビジネスパーソンが共感・参考にできる具体的な内容
- 「あるある」「意外な事実」「すぐ使えるコツ」などフック力の高いもの
- 各ポイントは独立して投稿として成立する内容

出力形式（JSON配列のみ）:
[
  {{"point": "ネタのポイント", "detail": "詳細や具体例（100字程度）", "hook_potential": "高/中/低"}},
  ...
]"""
    try:
        text = _call_claude(prompt, max_tokens=1500)
        points = _parse_json_points(text)
        logger.info(f"Claude-only research generated {len(points)} points for: {topic_name}")
        return points
    except Exception as e:
        logger.error(f"Claude-only research failed: {e}")
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

        # Web検索（リクエスト間に5秒待機）
        for kw in node["keywords"][:2]:
            results = brave_search(kw)
            for r in results:
                collected_text += f"\n{r['title']}\n{r['description']}\n"
            time.sleep(5)

        # YouTube検索
        for kw in node["keywords"][:1]:
            yt_results = youtube_search(kw)
            for r in yt_results:
                collected_text += f"\n{r['title']}\n{r['transcript']}\n"

        if not collected_text.strip():
            # 外部検索が全滅した場合はClaudeの知識で直接生成
            logger.warning(f"No content from external search for: {node['name']}, using Claude fallback")
            points = research_with_claude_only(node["name"], node["keywords"])
        else:
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
