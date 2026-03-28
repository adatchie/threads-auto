"""バズ研究 state の読み書きと要約を扱う共通ヘルパー"""
from __future__ import annotations

from collections import Counter
from typing import Any

from utils import load_json, save_json, STATE_DIR

BUZZ_REPORT_PATH = STATE_DIR / "buzz_insights.json"


def load_buzz_report() -> dict:
    if not BUZZ_REPORT_PATH.exists():
        return {}

    try:
        data = load_json(BUZZ_REPORT_PATH)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def save_buzz_report(report: dict) -> None:
    save_json(BUZZ_REPORT_PATH, report)


def _parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).split(",")

    items: list[str] = []
    for raw in raw_items:
        item = str(raw).strip()
        if item and item not in items:
            items.append(item)
    return items


def _matches_topic(insight: dict, topic: dict) -> bool:
    topic_tokens = {
        str(topic.get("topic_node", "")).strip(),
        str(topic.get("topic_name") or topic.get("name") or "").strip(),
        str(topic.get("category_id") or "").strip(),
        str(topic.get("category") or "").strip(),
    }
    insight_tokens = {
        str(insight.get("recommended_topic_node") or "").strip(),
        str(insight.get("topic_node") or "").strip(),
        str(insight.get("topic_name") or "").strip(),
        str(insight.get("category_id") or "").strip(),
        str(insight.get("category") or "").strip(),
    }
    return bool(topic_tokens & insight_tokens)


def summarize_buzz_report(report: dict, topic: dict | None = None, limit: int = 3) -> str:
    if not isinstance(report, dict):
        return "なし"

    insights = report.get("insights", [])
    if not insights:
        return "なし"

    related = [item for item in insights if topic is None or _matches_topic(item, topic)]
    if not related:
        related = insights

    lines: list[str] = []
    for item in related[:limit]:
        title = str(item.get("source_title") or item.get("source_url") or "unknown").strip()
        pattern = str(item.get("recommended_pattern") or item.get("hook_type") or "unknown").strip()
        mechanisms = _parse_list(item.get("mechanisms"))
        idea = str(
            item.get("adaptation_idea")
            or item.get("why_it_works")
            or item.get("summary")
            or ""
        ).strip()
        excerpt = str(item.get("source_excerpt") or "").strip()

        parts = [f"- {title} / {pattern}"]
        if mechanisms:
            parts.append(f"mech={', '.join(mechanisms[:4])}")
        if idea:
            parts.append(f"idea={idea[:100]}")
        if excerpt:
            parts.append(f"ref={excerpt[:100]}")
        lines.append(" | ".join(parts))

    return "\n".join(lines) if lines else "なし"


def derive_buzz_targets(report: dict) -> dict[str, set[str]]:
    boost_patterns: set[str] = set()
    boost_topics: set[str] = set()

    if not isinstance(report, dict):
        return {"boost_patterns": boost_patterns, "boost_topics": boost_topics}

    for pattern in _parse_list(report.get("pattern_bias")):
        boost_patterns.add(pattern)
    for topic in _parse_list(report.get("topic_bias")):
        boost_topics.add(topic)

    if not boost_patterns or not boost_topics:
        for item in report.get("insights", []):
            pattern = str(item.get("recommended_pattern") or item.get("hook_type") or "").strip()
            topic = str(item.get("recommended_topic_node") or "").strip()
            if pattern:
                boost_patterns.add(pattern)
            if topic:
                boost_topics.add(topic)

    return {
        "boost_patterns": boost_patterns,
        "boost_topics": boost_topics,
    }
