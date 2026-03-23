"""検索抽象レイヤー: Brave / Google Custom Search を環境に応じて切り替え"""
import os
import json
import logging
import time
import requests
from datetime import datetime, timedelta, timezone
from utils import load_json, save_json, STATE_DIR

logger = logging.getLogger("search")
JST = timezone(timedelta(hours=9))

SEARCH_CACHE_FILE = STATE_DIR / "search_cache.json"
CACHE_TTL_DAYS = 7


def _load_cache() -> dict:
    try:
        return load_json(SEARCH_CACHE_FILE)
    except Exception:
        return {}


def _save_cache(cache: dict):
    save_json(SEARCH_CACHE_FILE, cache)


def _now_jst() -> str:
    return datetime.now(JST).isoformat()


def _cache_get(cache: dict, query: str) -> list | None:
    entry = cache.get(query)
    if not entry:
        return None
    cached_at = datetime.fromisoformat(entry["cached_at"])
    if datetime.now(JST) - cached_at > timedelta(days=CACHE_TTL_DAYS):
        return None
    return entry["results"]


def _cache_set(cache: dict, query: str, results: list):
    cache[query] = {"cached_at": _now_jst(), "results": results}


def _detect_backend() -> str:
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        return "brave"
    if os.getenv("GOOGLE_CSE_KEY") and os.getenv("GOOGLE_CSE_ID"):
        return "google_cse"
    raise RuntimeError(
        "検索APIキーが未設定です（BRAVE_SEARCH_API_KEY または GOOGLE_CSE_KEY+GOOGLE_CSE_ID を設定してください）"
    )


def web_search(query: str, count: int = 5) -> list[dict]:
    """キャッシュ付きWeb検索。環境に応じてバックエンドを切り替え。"""
    cache = _load_cache()

    cached = _cache_get(cache, query)
    if cached is not None:
        logger.info(f"Search cache hit: {query}")
        return cached

    backend = _detect_backend()
    logger.debug(f"Using search backend: {backend}")

    if backend == "brave":
        results = _brave_search(query, count)
    elif backend == "google_cse":
        results = _google_cse_search(query, count)
    else:
        results = []

    if results:
        _cache_set(cache, query, results)
        _save_cache(cache)

    return results


def _brave_search(query: str, count: int) -> list[dict]:
    key = os.getenv("BRAVE_SEARCH_API_KEY")
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    params = {"q": query, "count": count}

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)

            remaining = resp.headers.get("X-RateLimit-Remaining") or resp.headers.get("X-Quota-Remaining")
            if remaining is not None:
                logger.info(f"Brave quota remaining: {remaining}")

            if resp.status_code == 200:
                results = resp.json().get("web", {}).get("results", [])
                return [
                    {"title": r["title"], "url": r["url"], "description": r.get("description", "")}
                    for r in results
                ]
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"Brave rate limited (attempt {attempt + 1}/3, retry-after={retry_after}s)")
                if attempt < 2:
                    time.sleep(retry_after)
            else:
                logger.error(f"Brave search HTTP {resp.status_code}: {resp.text[:200]}")
                return []
        except Exception as e:
            logger.error(f"Brave search error: {e}")
            return []

    logger.error("Brave search: 3回リトライ後も429 — クォータ枯渇の可能性あり")
    return []


def _google_cse_search(query: str, count: int) -> list[dict]:
    key = os.getenv("GOOGLE_CSE_KEY")
    cx = os.getenv("GOOGLE_CSE_ID")
    url = "https://customsearch.googleapis.com/customsearch/v1"
    params = {"key": key, "cx": cx, "q": query, "num": min(count, 10)}

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            return [
                {"title": r["title"], "url": r["link"], "description": r.get("snippet", "")}
                for r in items
            ]
        else:
            logger.error(f"Google CSE HTTP {resp.status_code}: {resp.text[:200]}")
            return []
    except Exception as e:
        logger.error(f"Google CSE error: {e}")
        return []
