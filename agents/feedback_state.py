"""運用フィードバック state の読み書きと要約を扱う共通ヘルパー"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from utils import load_json, save_json, STATE_DIR

JST = timezone(timedelta(hours=9))
FEEDBACK_PATH = STATE_DIR / "operator_feedback.json"


def load_operator_feedback() -> list[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    data = load_json(FEEDBACK_PATH)
    return data if isinstance(data, list) else []


def save_operator_feedback(entries: list[dict]) -> None:
    save_json(FEEDBACK_PATH, entries)


def parse_csv_list(value: Any) -> list[str]:
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


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except Exception:
        return None


def is_entry_active(entry: dict, now: datetime | None = None) -> bool:
    expires_at = _parse_iso(entry.get("expires_at"))
    if expires_at is None:
        return True
    now = now or datetime.now(JST)
    return now <= expires_at


def get_active_feedback(scope: str | None = None) -> list[dict]:
    entries = [entry for entry in load_operator_feedback() if is_entry_active(entry)]
    if not scope:
        return entries

    scope = scope.lower()
    return [
        entry
        for entry in entries
        if str(entry.get("scope", "overall")).lower() in {"overall", scope}
    ]


def summarize_operator_feedback(entries: list[dict], limit: int = 5) -> str:
    if not entries:
        return "なし"

    recent = entries[-limit:]
    lines = []
    for item in recent:
        mode = str(item.get("mode", "note")).strip() or "note"
        scope = str(item.get("scope", "overall")).strip() or "overall"
        topics = ", ".join(parse_csv_list(item.get("topics"))) or "なし"
        patterns = ", ".join(parse_csv_list(item.get("patterns"))) or "なし"
        note = str(item.get("note", "")).strip() or "メモなし"
        lines.append(f"- [{scope}/{mode}] topics={topics} patterns={patterns} | {note}")
    return "\n".join(lines)


def derive_operator_targets(entries: list[dict]) -> dict[str, set[str] | bool]:
    boost_topics: set[str] = set()
    avoid_topics: set[str] = set()
    boost_patterns: set[str] = set()
    avoid_patterns: set[str] = set()
    pause_generation = False

    for item in entries:
        mode = str(item.get("mode", "note")).strip().lower()
        topics = parse_csv_list(item.get("topics"))
        patterns = parse_csv_list(item.get("patterns"))

        if mode == "pause":
            pause_generation = True
        elif mode == "boost":
            boost_topics.update(topics)
            boost_patterns.update(patterns)
        elif mode == "avoid":
            avoid_topics.update(topics)
            avoid_patterns.update(patterns)

    return {
        "boost_topics": boost_topics,
        "avoid_topics": avoid_topics,
        "boost_patterns": boost_patterns,
        "avoid_patterns": avoid_patterns,
        "pause_generation": pause_generation,
    }


def topic_tokens(topic: dict) -> set[str]:
    return {
        str(value).strip()
        for value in [
            topic.get("topic_node"),
            topic.get("topic_name"),
            topic.get("name"),
            topic.get("category_id"),
            topic.get("category"),
        ]
        if value
    }


def pattern_tokens(pattern: dict) -> set[str]:
    return {
        str(value).strip()
        for value in [
            pattern.get("id"),
            pattern.get("name"),
        ]
        if value
    }
