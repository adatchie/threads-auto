"""共通ユーティリティ"""
import json
import os
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_DIR = Path(__file__).parent.parent
STATE_DIR = BASE_DIR / "state"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"),
        logging.StreamHandler(),
    ],
)


def load_json(path: Path) -> list | dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: list | dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_jst() -> str:
    from datetime import timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).isoformat()


def log_error(agent: str, message: str, detail: str = "") -> None:
    errors = load_json(STATE_DIR / "error_log.json")
    errors.append({
        "timestamp": now_jst(),
        "agent": agent,
        "message": message,
        "detail": detail,
    })
    save_json(STATE_DIR / "error_log.json", errors)


def is_kill_switch_on() -> bool:
    ks = load_json(STATE_DIR / "kill_switch.json")
    return ks.get("enabled", False)
