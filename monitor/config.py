"""讀取 .env 設定。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip().strip('"').strip("'")


# --- AI ---
OPENAI_API_KEY = _str("OPENAI_API_KEY")
OPENAI_BASE_URL = _str("OPENAI_BASE_URL", "https://api.openai.com/v1/")
OPENAI_MODEL_NAME = _str("OPENAI_MODEL_NAME", "gpt-4o-mini")
AI_IMAGE_MODE = _str("AI_IMAGE_MODE", "base64").lower()  # base64 | url | none
AI_MAX_IMAGES = _int("AI_MAX_IMAGES", 4)
AI_RESPONSE_FORMAT_JSON = _bool("AI_RESPONSE_FORMAT_JSON", True)
AI_TIMEOUT_SECONDS = _int("AI_TIMEOUT_SECONDS", 120)
AI_DEBUG = _bool("AI_DEBUG", False)
AI_PROXY_URL = _str("AI_PROXY_URL")

AI_ENABLED = bool(OPENAI_API_KEY) and not OPENAI_API_KEY.startswith("sk-...")

# --- 爬取 ---
REQUEST_DELAY_SECONDS = _float("REQUEST_DELAY_SECONDS", 3.0)
SCRAPE_PROXY_URL = _str("SCRAPE_PROXY_URL")
MIN_INTERVAL_MINUTES = _int("MIN_INTERVAL_MINUTES", 5)
MAX_AI_PER_RUN = _int("MAX_AI_PER_RUN", 20)
MARKET_CACHE_HOURS = _int("MARKET_CACHE_HOURS", 6)

# --- 匯率顯示（可選） ---
LOCAL_CURRENCY = _str("LOCAL_CURRENCY", "TWD")
JPY_TO_LOCAL_RATE = _float("JPY_TO_LOCAL_RATE", 0.0)  # 例如 0.21；0 表示不顯示

# --- Web ---
SERVER_HOST = _str("SERVER_HOST", "127.0.0.1")
SERVER_PORT = _int("SERVER_PORT", 8000)
WEB_USERNAME = _str("WEB_USERNAME", "admin")
WEB_PASSWORD = _str("WEB_PASSWORD")

# --- 通知 ---
NTFY_TOPIC_URL = _str("NTFY_TOPIC_URL")
TELEGRAM_BOT_TOKEN = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _str("TELEGRAM_CHAT_ID")
TELEGRAM_API_BASE_URL = _str("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
DISCORD_WEBHOOK_URL = _str("DISCORD_WEBHOOK_URL")
BARK_URL = _str("BARK_URL")
WEBHOOK_URL = _str("WEBHOOK_URL")

# --- 路徑 ---
DATA_DIR = BASE_DIR / "data"
DB_FILE = Path(_str("DB_FILE") or (DATA_DIR / "app.sqlite3"))
TASKS_FILE = BASE_DIR / "tasks.json"
PROMPTS_DIR = BASE_DIR / "prompts"
LOG_DIR = BASE_DIR / "logs"

for _d in (DATA_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def fmt_jpy(v: int | float | None) -> str:
    if v is None:
        return "-"
    s = f"¥{int(v):,}"
    if JPY_TO_LOCAL_RATE > 0:
        s += f"（約 {LOCAL_CURRENCY} {v * JPY_TO_LOCAL_RATE:,.0f}）"
    return s
