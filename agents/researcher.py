"""
リサーチャー: テーマツリーの補完とバズ投稿研究を回す
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
from buzz_state import save_buzz_report

logger = logging.getLogger("researcher")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
JST = timezone(timedelta(hours=9))
BUZZ_TARGET_NODE_LIMIT = 2
BUZZ_RESULTS_PER_QUERY = 4
BUZZ_MAX_CANDIDATES = 8
BUZZ_PLATFORMS = ("x.com", "threads.net")


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
                "topic_node": nid,
                "name": node["name"],
                "topic_name": node["name"],
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


def build_topic_lookup(topic_tree: dict) -> dict:
    """topic_node と topic_name を逆引きできるようにする"""
    nodes = []
    node_by_id = {}
    node_by_name = {}

    for category in topic_tree.get("categories", []):
        category_id = category.get("id", "")
        category_name = category.get("name", "")
        for node in category.get("nodes", []):
            node_id = node.get("id", "")
            node_name = node.get("name", "")
            if not node_id:
                continue
            entry = {
                "id": node_id,
                "topic_node": node_id,
                "name": node_name,
                "topic_name": node_name,
                "keywords": node.get("keywords", []),
                "category_id": category_id,
                "category": category_name,
                "priority": node.get("priority", 99),
            }
            nodes.append(entry)
            node_by_id[node_id] = entry
            node_by_name[node_name] = entry

    return {
        "nodes": nodes,
        "node_by_id": node_by_id,
        "node_by_name": node_by_name,
    }


def select_buzz_focus_nodes(topic_tree: dict, counts: Counter, analyst_report: dict, operator_targets: dict, max_nodes: int = BUZZ_TARGET_NODE_LIMIT) -> list[dict]:
    """分析結果と運用フィードバックを踏まえて、バズ研究対象を選ぶ"""
    topic_lookup = build_topic_lookup(topic_tree)
    ranked_nodes = find_underrepresented_nodes(
        topic_tree,
        counts,
        operator_targets.get("boost_topics"),
        operator_targets.get("avoid_topics"),
    )

    preferred_tokens = set(analyst_report.get("top_topics", []))
    preferred_tokens.update(operator_targets.get("boost_topics", set()))

    chosen: list[dict] = []
    if preferred_tokens:
        for node in ranked_nodes:
            if topic_tokens(node) & preferred_tokens:
                chosen.append(node)
            if len(chosen) >= max_nodes:
                break

    if len(chosen) < max_nodes:
        for node in ranked_nodes:
            if node in chosen:
                continue
            chosen.append(node)
            if len(chosen) >= max_nodes:
                break

    if not chosen:
        chosen = topic_lookup["nodes"][:max_nodes]

    return chosen[:max_nodes]


def build_buzz_queries(node: dict) -> list[str]:
    """バズ研究用の検索クエリを作る"""
    queries = []
    topic_name = node.get("topic_name") or node.get("name") or ""
    keywords = node.get("keywords", [])[:2]

    if topic_name:
        queries.append(f"site:x.com {topic_name}")
        queries.append(f"site:x.com/i/trending {topic_name}")
        queries.append(f"site:threads.net {topic_name}")

    for keyword in keywords:
        queries.append(f"site:x.com {keyword}")
        queries.append(f"site:x.com/i/trending {keyword}")
        queries.append(f"site:threads.net {keyword}")

    deduped = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    return deduped


def score_buzz_candidate(candidate: dict, node: dict) -> float:
    """検索結果がどれだけバズ研究向きかを雑に採点する"""
    text = " ".join(
        str(candidate.get(field, ""))
        for field in ("title", "description", "url")
    ).lower()
    score = 0.0

    url = str(candidate.get("url", ""))
    if "x.com" in url:
        score += 2.0
    if "threads.net" in url:
        score += 1.5
    if "/status/" in url:
        score += 3.0
    if "/i/trending/" in url:
        score += 2.5
    if any(term in text for term in ["views", "閲覧", "いいね", "likes", "reposts", "comments", "reply"]):
        score += 2.0
    if any(term in text for term in ["裏切り", "本音", "嘘", "注意", "比較", "体験", "続き", "コメント", "需要"]):
        score += 1.5

    node_tokens = topic_tokens(node)
    if any(token and token in text for token in node_tokens):
        score += 1.0
    if node.get("topic_name") and node["topic_name"] in text:
        score += 1.0
    if any(keyword in text for keyword in node.get("keywords", [])[:2]):
        score += 1.0

    return score


def collect_buzz_candidates(nodes: list[dict]) -> list[dict]:
    """X/Threads の検索結果からバズ候補を集める"""
    candidates: list[dict] = []
    seen_urls: set[str] = set()

    for node in nodes:
        queries = build_buzz_queries(node)
        for query in queries:
            try:
                results = web_search(query, count=BUZZ_RESULTS_PER_QUERY)
            except Exception as e:
                logger.error(f"Buzz search failed for '{query}': {e}")
                continue

            for result in results:
                url = str(result.get("url", "")).strip()
                if not url or url in seen_urls:
                    continue
                if not any(domain in url for domain in BUZZ_PLATFORMS):
                    continue

                score = score_buzz_candidate(result, node)
                if score <= 0:
                    continue

                seen_urls.add(url)
                candidates.append({
                    "topic_node": node.get("topic_node", node.get("id", "")),
                    "topic_name": node.get("topic_name") or node.get("name", ""),
                    "category_id": node.get("category_id", ""),
                    "category": node.get("category", ""),
                    "query": query,
                    "platform": "threads" if "threads.net" in url else "x",
                    "title": result.get("title", ""),
                    "url": url,
                    "description": result.get("description", "")[:500],
                    "score": round(score, 2),
                })

            if results:
                time.sleep(1)

    candidates.sort(key=lambda item: (item.get("score", 0.0), len(item.get("description", ""))), reverse=True)
    return candidates[:BUZZ_MAX_CANDIDATES]


def _normalize_pattern_list(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in items:
            items.append(item)
    return items


def _heuristic_buzz_analysis(candidates: list[dict]) -> dict:
    """LLMが失敗したときの簡易フォールバック"""
    if not candidates:
        return {
            "summary_text": "外部バズ候補を取得できませんでした。",
            "pattern_bias": [],
            "topic_bias": [],
            "insights": [],
        }

    insights = []
    pattern_bias = []
    topic_bias = []
    for candidate in candidates[:3]:
        text = f"{candidate.get('title', '')} {candidate.get('description', '')}".lower()
        if "コメント" in text or "comment" in text or "続き" in text:
            pattern = "comment_inducing"
        elif any(term in text for term in ["裏切り", "嘘", "本音", "真実"]):
            pattern = "rebuttal"
        elif any(term in text for term in ["比較", "違い", "対比"]):
            pattern = "comparison"
        elif any(term in text for term in ["注意", "失敗", "やるな", "危険"]):
            pattern = "warning"
        elif any(term in text for term in ["3つ", "5つ", "10", "個"]):
            pattern = "list"
        else:
            pattern = "buzz_pivot"

        pattern_bias.append(pattern)
        topic_bias.append(candidate.get("topic_node", ""))
        insights.append({
            "source_title": candidate.get("title", ""),
            "source_url": candidate.get("url", ""),
            "platform": candidate.get("platform", "x"),
            "source_excerpt": candidate.get("description", ""),
            "hook_type": pattern,
            "mechanisms": ["heuristic"],
            "why_it_works": "検索結果の文面から、反論・対比・警告・続き誘導のいずれかが強いと判断したため。",
            "adaptation_idea": f"{candidate.get('topic_name', '')}向けに {pattern} 型として転用する。",
            "recommended_pattern": pattern,
            "recommended_topic_node": candidate.get("topic_node", ""),
            "confidence": 0.45,
        })

    return {
        "summary_text": "外部バズ候補から、反論・対比・続き誘導・警告の構造が多いと推定しました。",
        "pattern_bias": _normalize_pattern_list(pattern_bias),
        "topic_bias": _normalize_pattern_list(topic_bias),
        "insights": insights,
    }


def analyze_buzz_candidates(candidates: list[dict], focus_nodes: list[dict]) -> dict:
    """検索候補をLLMで構造分析し、次回生成用のバズメモにする"""
    if not candidates:
        return {
            "summary_text": "外部バズ候補を取得できませんでした。",
            "pattern_bias": [],
            "topic_bias": [],
            "insights": [],
        }

    patterns = load_json(KNOWLEDGE_DIR / "post_patterns.json")["patterns"]
    allowed_patterns = [pattern["id"] for pattern in patterns]
    topic_map = {node["topic_node"]: node["topic_name"] for node in focus_nodes}

    prompt = f"""あなたはThreads/Xの外部バズ投稿を分析するリサーチャーです。
目的は、転職系Threadsの次回投稿を改善するために、実際に伸びている投稿の「構造」を抽出することです。

【利用可能なパターンID】
{json.dumps(allowed_patterns, ensure_ascii=False)}

【対象トピックノード】
{json.dumps(topic_map, ensure_ascii=False)}

【検索候補】
{json.dumps(candidates, ensure_ascii=False)}

以下の観点で分析してください。
- 1行目のフックの種類
- コメントや保存を誘発する仕掛け
- 具体性、数字、対比、権威、反論などの使い方
- 次回の転職系投稿にどう転用するか

出力は JSON のみで、次の形式にしてください。
{{
  "summary_text": "全体の共通点を2〜4文で",
  "pattern_bias": ["rebuttal", "warning"],
  "topic_bias": ["fail_reasons"],
  "insights": [
    {{
      "source_title": "元の検索結果タイトル",
      "source_url": "元のURL",
      "platform": "x または threads",
      "source_excerpt": "検索結果の抜粋",
      "hook_type": "rebuttal",
      "mechanisms": ["対比", "数字", "続き誘導"],
      "why_it_works": "なぜ伸びやすいか",
      "adaptation_idea": "転職系Threadsへどう転用するか",
      "recommended_pattern": "rebuttal",
      "recommended_topic_node": "fail_reasons",
      "confidence": 0.82
    }}
  ]
}}"""

    try:
        raw_report = call_llm_json(prompt, max_tokens=1400)
    except Exception as e:
        logger.warning(f"Buzz analysis LLM failed: {e}")
        return _heuristic_buzz_analysis(candidates)

    if not isinstance(raw_report, dict):
        return _heuristic_buzz_analysis(candidates)

    insight_by_url = {item.get("url", ""): item for item in candidates}
    insights: list[dict] = []
    for item in raw_report.get("insights", []) if isinstance(raw_report.get("insights", []), list) else []:
        url = str(item.get("source_url") or item.get("url") or "").strip()
        source = insight_by_url.get(url, {})
        insight = {
            "source_title": str(item.get("source_title") or source.get("title") or "").strip(),
            "source_url": url,
            "platform": str(item.get("platform") or source.get("platform") or "x").strip(),
            "source_excerpt": str(item.get("source_excerpt") or source.get("description") or "").strip(),
            "hook_type": str(item.get("hook_type") or item.get("recommended_pattern") or "").strip(),
            "mechanisms": item.get("mechanisms") if isinstance(item.get("mechanisms"), list) else [],
            "why_it_works": str(item.get("why_it_works") or "").strip(),
            "adaptation_idea": str(item.get("adaptation_idea") or "").strip(),
            "recommended_pattern": str(item.get("recommended_pattern") or item.get("hook_type") or "").strip(),
            "recommended_topic_node": str(item.get("recommended_topic_node") or source.get("topic_node") or "").strip(),
            "confidence": float(item.get("confidence") or 0.5),
        }
        if not insight["source_url"]:
            continue
        if not insight["source_title"]:
            insight["source_title"] = insight["source_url"]
        insights.append(insight)

    if not insights:
        return _heuristic_buzz_analysis(candidates)

    pattern_bias = _normalize_pattern_list([
        str(value).strip()
        for value in raw_report.get("pattern_bias", [])
        if str(value).strip()
    ])
    topic_bias = _normalize_pattern_list([
        str(value).strip()
        for value in raw_report.get("topic_bias", [])
        if str(value).strip()
    ])

    if not pattern_bias:
        pattern_bias = _normalize_pattern_list([
            str(item.get("recommended_pattern") or item.get("hook_type") or "").strip()
            for item in insights
            if str(item.get("recommended_pattern") or item.get("hook_type") or "").strip()
        ])
    if not topic_bias:
        topic_bias = _normalize_pattern_list([
            str(item.get("recommended_topic_node") or "").strip()
            for item in insights
            if str(item.get("recommended_topic_node") or "").strip()
        ])

    return {
        "generated_at": now_jst(),
        "analysis_mode": "buzz",
        "source_count": len(candidates),
        "insight_count": len(insights),
        "focus_nodes": focus_nodes,
        "summary_text": str(raw_report.get("summary_text") or "").strip() or "外部バズの共通点を抽出しました。",
        "pattern_bias": pattern_bias,
        "topic_bias": topic_bias,
        "insights": insights,
    }


def run_buzz_research(topic_tree: dict, counts: Counter, analyst_report: dict, operator_targets: dict, max_nodes: int = BUZZ_TARGET_NODE_LIMIT) -> dict:
    """外部バズ投稿の研究を実行する"""
    focus_nodes = select_buzz_focus_nodes(topic_tree, counts, analyst_report, operator_targets, max_nodes=max_nodes)
    candidates = collect_buzz_candidates(focus_nodes)
    report = analyze_buzz_candidates(candidates, focus_nodes)
    if isinstance(report, dict):
        report["focus_nodes"] = focus_nodes
    save_buzz_report(report)
    logger.info(
        "Buzz research done. focus_nodes=%s, candidates=%s, insights=%s",
        [node.get("topic_node") for node in focus_nodes],
        len(candidates),
        len(report.get("insights", [])),
    )
    return report


def inject_buzz_insights_into_pool(pool: list, report: dict) -> int:
    """バズ研究の要点を research_pool に流し込む"""
    if not isinstance(report, dict):
        return 0

    insights = report.get("insights", [])
    if not insights:
        return 0

    focus_map = {
        node.get("topic_node"): node
        for node in report.get("focus_nodes", [])
        if isinstance(node, dict) and node.get("topic_node")
    }
    existing_ids = {item.get("id") for item in pool if item.get("id")}
    added = 0

    for idx, insight in enumerate(insights[:BUZZ_TARGET_NODE_LIMIT * 2]):
        topic_node = str(
            insight.get("recommended_topic_node")
            or insight.get("topic_node")
            or ""
        ).strip()
        focus = focus_map.get(topic_node) or (report.get("focus_nodes", [])[:1] or [None])[0]
        if not isinstance(focus, dict):
            continue

        topic_node = topic_node or str(focus.get("topic_node") or "").strip()
        if not topic_node:
            continue

        topic_name = str(
            focus.get("topic_name")
            or focus.get("name")
            or insight.get("topic_name")
            or ""
        ).strip()
        category_id = str(focus.get("category_id") or insight.get("category_id") or "").strip()
        category = str(focus.get("category") or insight.get("category") or "").strip()
        item_id = f"buzz_{topic_node}_{now_jst()[:10]}_{idx}"
        if item_id in existing_ids:
            continue

        mechanisms = insight.get("mechanisms", [])
        if isinstance(mechanisms, list):
            mechanism_text = " / ".join(str(m).strip() for m in mechanisms if str(m).strip())
        else:
            mechanism_text = str(mechanisms).strip()

        point = str(insight.get("adaptation_idea") or insight.get("why_it_works") or insight.get("source_title") or "").strip()
        detail_bits = [
            f"外部バズ: {insight.get('source_title', '')}",
            f"hook={insight.get('hook_type', '')}",
            f"mechanisms={mechanism_text or 'なし'}",
            f"source={insight.get('source_url', '')}",
        ]

        pool.append({
            "id": item_id,
            "topic_node": topic_node,
            "topic_name": topic_name,
            "category_id": category_id,
            "category": category,
            "source_type": "buzz_research",
            "point": point or "外部バズの構造を転用する",
            "detail": " | ".join(bit for bit in detail_bits if bit),
            "hook_potential": "高",
            "collected_at": now_jst(),
            "used": False,
            "buzz_source_url": insight.get("source_url", ""),
            "buzz_source_title": insight.get("source_title", ""),
            "buzz_hook_type": insight.get("hook_type", ""),
        })
        existing_ids.add(item_id)
        added += 1

    return added


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


def run_topic_research(topic_tree: dict, history: list, pool: list, operator_targets: dict, max_nodes: int = 3) -> int:
    """通常のテーマ研究を実行する"""
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
                "source_type": "topic_research",
                "point": point.get("point", ""),
                "detail": point.get("detail", ""),
                "hook_potential": point.get("hook_potential", "中"),
                "collected_at": now_jst(),
                "used": False,
            })

    pool.extend(new_items)
    save_json(STATE_DIR / "research_pool.json", pool)
    logger.info(f"Topic research done. Added {len(new_items)} items to research pool.")
    return len(new_items)


def run(mode: str = "combined", max_nodes: int = 3, buzz_max_nodes: int = BUZZ_TARGET_NODE_LIMIT):
    logger.info(f"Researcher started (mode={mode})")
    if is_kill_switch_on():
        logger.warning("KILL_SWITCH is enabled. Researcher aborted.")
        return

    history = load_json(STATE_DIR / "post_history.json")
    topic_tree = load_json(KNOWLEDGE_DIR / "topic_tree.json")
    pool = load_json(STATE_DIR / "research_pool.json")
    operator_feedback = get_active_feedback("research")
    operator_targets = derive_operator_targets(operator_feedback)
    analyst_report_path = STATE_DIR / "analyst_report.json"
    analyst_report = load_json(analyst_report_path) if analyst_report_path.exists() else {}

    counts = get_recent_topic_counts(history)
    buzz_items = 0
    topic_items = 0

    if mode in {"combined", "buzz"}:
        previous_buzz = run_buzz_research(
            topic_tree,
            counts,
            analyst_report,
            operator_targets,
            max_nodes=buzz_max_nodes,
        )
        buzz_items = len(previous_buzz.get("insights", []))
        buzz_added = inject_buzz_insights_into_pool(pool, previous_buzz)
        if buzz_added:
            save_json(STATE_DIR / "research_pool.json", pool)
            logger.info("Injected %s buzz insights into research pool.", buzz_added)

    if mode in {"combined", "topic"}:
        topic_items = run_topic_research(topic_tree, history, pool, operator_targets, max_nodes=max_nodes)

    logger.info(
        "Researcher done. buzz_insights=%s, topic_items=%s",
        buzz_items,
        topic_items,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["combined", "topic", "buzz"], default="combined")
    parser.add_argument("--max-nodes", type=int, default=3)
    parser.add_argument("--buzz-max-nodes", type=int, default=BUZZ_TARGET_NODE_LIMIT)
    args = parser.parse_args()
    run(mode=args.mode, max_nodes=args.max_nodes, buzz_max_nodes=args.buzz_max_nodes)
