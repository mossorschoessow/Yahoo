"""設定：讀取 .env，並提供給設定頁修改 / 重新載入。

路徑規則：
- 程式資源（static、預設 prompt）：打包後在 PyInstaller 解壓目錄，原始碼執行時在專案目錄
- 使用者資料（.env、tasks.json、prompts、data、logs）：
    打包後放在 %LOCALAPPDATA%\\YahooAuctionMonitor；原始碼執行時放在專案目錄
    也可以用環境變數 YAM_HOME 指定
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

APP_NAME = "YahooAuctionMonitor"
APP_TITLE = "日拍撿漏監控"
FROZEN = bool(getattr(sys, "frozen", False))

RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
if os.getenv("YAM_HOME"):
    HOME_DIR = Path(os.environ["YAM_HOME"])
elif FROZEN:
    HOME_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home()) / APP_NAME
else:
    HOME_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = HOME_DIR  # 舊名稱相容

STATIC_DIR = RESOURCE_DIR / "static"
ENV_FILE = HOME_DIR / ".env"
TASKS_FILE = HOME_DIR / "tasks.json"
PROMPTS_DIR = HOME_DIR / "prompts"
DATA_DIR = HOME_DIR / "data"
LOG_DIR = HOME_DIR / "logs"

for _d in (HOME_DIR, PROMPTS_DIR, DATA_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

PROMPT_FILES = {"zh-TW": "base_prompt.txt", "zh-CN": "base_prompt.zh-CN.txt"}


def _first_run_files() -> None:
    """第一次執行：把預設檔案複製到使用者資料夾。"""
    for name in PROMPT_FILES.values():
        src, dst = RESOURCE_DIR / "prompts" / name, PROMPTS_DIR / name
        if src.exists() and not dst.exists() and src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
    src = RESOURCE_DIR / "tasks.example.json"
    if src.exists() and not TASKS_FILE.exists() and src.resolve() != TASKS_FILE.resolve():
        tasks = json.loads(src.read_text(encoding="utf-8"))
        from .i18n import tr
        for t in tasks:
            t["enabled"] = False  # 範例任務先停用，等使用者設定好再自己打開
            for k in ("name", "description"):
                if t.get(k):
                    t[k] = tr(t[k])
        TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def prompt_file() -> Path:
    """目前語言的 AI 提示詞檔案。"""
    return PROMPTS_DIR / PROMPT_FILES.get(LANG, PROMPT_FILES["zh-TW"])


def detect_system_lang() -> str:
    """跟隨系統語言：簡體中文環境用 zh-CN，其他用 zh-TW。"""
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # 0x0804 中國、0x1004 新加坡、0x7804 / 0x0004 簡體中文
            return "zh-CN" if lcid in (0x0804, 0x1004, 0x0004, 0x7804) else "zh-TW"
        except Exception:
            return "zh-TW"
    env = (os.getenv("LC_ALL") or os.getenv("LANG") or "").lower()
    return "zh-CN" if env.startswith(("zh_cn", "zh-cn", "zh_sg", "zh_hans")) else "zh-TW"


# 啟動前就存在的環境變數優先於 .env（方便測試 / Docker）
_PROCESS_ENV = {k: v for k, v in os.environ.items()}

# --------------------------------------------------------------------------- 設定欄位定義
# (key, 預設值, 型別, 分組, 標籤, 說明)
SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {"key": "OPENAI_API_KEY", "default": "", "type": "secret", "group": "AI 模型", "label": "API Key", "help": "沒填就用規則模式（只比價格）"},
    {"key": "OPENAI_BASE_URL", "default": "https://api.openai.com/v1/", "type": "str", "group": "AI 模型", "label": "API 位址", "help": "任何 OpenAI 相容服務"},
    {"key": "OPENAI_MODEL_NAME", "default": "gpt-4o-mini", "type": "str", "group": "AI 模型", "label": "模型名稱", "help": "必須支援圖片輸入"},
    {"key": "AI_IMAGE_MODE", "default": "base64", "type": "choice", "choices": ["base64", "url", "none"], "group": "AI 模型", "label": "圖片傳送方式", "help": "base64 最相容；url 省流量；none 不傳圖"},
    {"key": "AI_MAX_IMAGES", "default": "4", "type": "int", "group": "AI 模型", "label": "每件商品最多幾張圖"},
    {"key": "AI_RESPONSE_FORMAT_JSON", "default": "true", "type": "bool", "group": "AI 模型", "label": "使用 JSON 回覆格式", "help": "模型不支援時會自動關閉"},
    {"key": "AI_PROXY_URL", "default": "", "type": "str", "group": "AI 模型", "label": "AI 代理", "help": "例：http://127.0.0.1:7890"},
    {"key": "TELEGRAM_BOT_TOKEN", "default": "", "type": "secret", "group": "通知", "label": "Telegram Bot Token", "help": "找 @BotFather 建立"},
    {"key": "TELEGRAM_CHAT_ID", "default": "", "type": "str", "group": "通知", "label": "Telegram Chat ID", "help": "找 @userinfobot 查詢"},
    {"key": "DISCORD_WEBHOOK_URL", "default": "", "type": "secret", "group": "通知", "label": "Discord Webhook"},
    {"key": "NTFY_TOPIC_URL", "default": "", "type": "str", "group": "通知", "label": "ntfy 主題網址", "help": "例：https://ntfy.sh/my-topic-xxxx"},
    {"key": "BARK_URL", "default": "", "type": "secret", "group": "通知", "label": "Bark 網址（iOS）"},
    {"key": "WEBHOOK_URL", "default": "", "type": "str", "group": "通知", "label": "通用 Webhook"},
    {"key": "TELEGRAM_API_BASE_URL", "default": "https://api.telegram.org", "type": "str", "group": "通知", "label": "Telegram API 位址"},
    {"key": "UI_LANGUAGE", "default": "auto", "type": "choice", "choices": ["auto", "zh-TW", "zh-CN"],
     "choice_labels": ["跟隨系統 / 跟随系统", "繁體中文", "简体中文"], "group": "顯示", "label": "介面語言",
     "help": "通知與 AI 回覆也會使用這個語言"},
    {"key": "LOCAL_CURRENCY", "default": "TWD", "type": "str", "group": "顯示", "label": "換算幣別"},
    {"key": "JPY_TO_LOCAL_RATE", "default": "0", "type": "float", "group": "顯示", "label": "日圓匯率", "help": "1 日圓 = ? 換算幣別；0 不顯示"},
    {"key": "REQUEST_DELAY_SECONDS", "default": "3", "type": "float", "group": "爬取", "label": "請求間隔（秒）", "help": "不要調太小，避免被擋"},
    {"key": "MIN_INTERVAL_MINUTES", "default": "5", "type": "int", "group": "爬取", "label": "任務最短間隔（分鐘）"},
    {"key": "MAX_AI_PER_RUN", "default": "20", "type": "int", "group": "爬取", "label": "每次最多分析幾件", "help": "控制 AI 花費"},
    {"key": "MARKET_CACHE_HOURS", "default": "6", "type": "int", "group": "爬取", "label": "行情快取（小時）"},
    {"key": "SCRAPE_PROXY_URL", "default": "", "type": "str", "group": "爬取", "label": "日拍代理"},
    {"key": "SERVER_HOST", "default": "127.0.0.1", "type": "str", "group": "Web", "label": "監聽位址", "help": "改成 0.0.0.0 可讓手機連線（務必設密碼），重開生效"},
    {"key": "SERVER_PORT", "default": "8000", "type": "int", "group": "Web", "label": "連接埠", "help": "重開生效"},
    {"key": "WEB_USERNAME", "default": "admin", "type": "str", "group": "Web", "label": "登入帳號"},
    {"key": "WEB_PASSWORD", "default": "", "type": "secret", "group": "Web", "label": "登入密碼", "help": "留空 = 不需登入"},
]
_SCHEMA_BY_KEY = {s["key"]: s for s in SETTINGS_SCHEMA}
_EXTRA_KEYS = ["AI_TIMEOUT_SECONDS", "AI_DEBUG", "DB_FILE"]


def _raw_values() -> dict[str, str]:
    file_vals = {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()} if ENV_FILE.exists() else {}
    vals = {s["key"]: s["default"] for s in SETTINGS_SCHEMA}
    vals.update(file_vals)
    for k in list(vals) + _EXTRA_KEYS:
        if k in _PROCESS_ENV:
            vals[k] = _PROCESS_ENV[k]
    return {k: str(v).strip().strip('"').strip("'") for k, v in vals.items()}


def _b(v: str, d: bool) -> bool:
    return d if v == "" else v.lower() in ("1", "true", "yes", "on")


def _i(v: str, d: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _f(v: str, d: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def reload() -> None:
    """重新讀取 .env 並更新本模組的設定值。"""
    v = _raw_values()
    g = globals()
    g.update(
        OPENAI_API_KEY=v.get("OPENAI_API_KEY", ""),
        OPENAI_BASE_URL=v.get("OPENAI_BASE_URL") or "https://api.openai.com/v1/",
        OPENAI_MODEL_NAME=v.get("OPENAI_MODEL_NAME") or "gpt-4o-mini",
        AI_IMAGE_MODE=(v.get("AI_IMAGE_MODE") or "base64").lower(),
        AI_MAX_IMAGES=_i(v.get("AI_MAX_IMAGES", ""), 4),
        AI_RESPONSE_FORMAT_JSON=_b(v.get("AI_RESPONSE_FORMAT_JSON", ""), True),
        AI_TIMEOUT_SECONDS=_i(v.get("AI_TIMEOUT_SECONDS", ""), 120),
        AI_DEBUG=_b(v.get("AI_DEBUG", ""), False),
        AI_PROXY_URL=v.get("AI_PROXY_URL", ""),
        REQUEST_DELAY_SECONDS=_f(v.get("REQUEST_DELAY_SECONDS", ""), 3.0),
        SCRAPE_PROXY_URL=v.get("SCRAPE_PROXY_URL", ""),
        MIN_INTERVAL_MINUTES=_i(v.get("MIN_INTERVAL_MINUTES", ""), 5),
        MAX_AI_PER_RUN=_i(v.get("MAX_AI_PER_RUN", ""), 20),
        MARKET_CACHE_HOURS=_i(v.get("MARKET_CACHE_HOURS", ""), 6),
        LOCAL_CURRENCY=v.get("LOCAL_CURRENCY") or "TWD",
        JPY_TO_LOCAL_RATE=_f(v.get("JPY_TO_LOCAL_RATE", ""), 0.0),
        SERVER_HOST=v.get("SERVER_HOST") or "127.0.0.1",
        SERVER_PORT=_i(v.get("SERVER_PORT", ""), 8000),
        WEB_USERNAME=v.get("WEB_USERNAME") or "admin",
        WEB_PASSWORD=v.get("WEB_PASSWORD", ""),
        NTFY_TOPIC_URL=v.get("NTFY_TOPIC_URL", ""),
        TELEGRAM_BOT_TOKEN=v.get("TELEGRAM_BOT_TOKEN", ""),
        TELEGRAM_CHAT_ID=v.get("TELEGRAM_CHAT_ID", ""),
        TELEGRAM_API_BASE_URL=v.get("TELEGRAM_API_BASE_URL") or "https://api.telegram.org",
        DISCORD_WEBHOOK_URL=v.get("DISCORD_WEBHOOK_URL", ""),
        BARK_URL=v.get("BARK_URL", ""),
        WEBHOOK_URL=v.get("WEBHOOK_URL", ""),
        DB_FILE=Path(v.get("DB_FILE") or (DATA_DIR / "app.sqlite3")),
    )
    key = g["OPENAI_API_KEY"]
    g["AI_ENABLED"] = bool(key) and not key.startswith("sk-...")
    ui = v.get("UI_LANGUAGE") or "auto"
    g["UI_LANGUAGE"] = ui
    g["LANG"] = ui if ui in ("zh-TW", "zh-CN") else detect_system_lang()


def get_settings() -> list[dict[str, Any]]:
    v = _raw_values()
    from .i18n import tr
    out = []
    for s in SETTINGS_SCHEMA:
        item = {**s, "value": v.get(s["key"], s["default"]), "locked": s["key"] in _PROCESS_ENV}
        for k in ("group", "label", "help"):
            if item.get(k):
                item[k] = tr(item[k])
        out.append(item)
    return out


def save_settings(new: dict[str, Any]) -> None:
    """寫回 .env（只寫設定頁認得的欄位，其他既有欄位保留）。"""
    current = {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()} if ENV_FILE.exists() else {}
    for k, val in new.items():
        s = _SCHEMA_BY_KEY.get(k)
        if not s:
            continue
        val = "" if val is None else str(val).strip()
        if s["type"] == "bool":
            val = "true" if val.lower() in ("1", "true", "yes", "on") else "false"
        elif s["type"] in ("int", "float") and val:
            try:
                float(val)
            except ValueError:
                raise ValueError(_tr("「{label}」必須是數字").format(label=_tr(s["label"])))
        elif s["type"] == "choice" and val not in s["choices"]:
            raise ValueError(_tr("「{label}」的值不正確").format(label=_tr(s["label"])))
        if "\n" in val:
            raise ValueError(_tr("「{label}」不能換行").format(label=_tr(s["label"])))
        current[k] = val

    lines = ["# 由「日拍撿漏監控」設定頁產生，也可以手動編輯", ""]
    group = None
    for s in SETTINGS_SCHEMA:
        if s["group"] != group:
            group = s["group"]
            lines += ["", f"# ---- {group} ----"]
        lines.append(f"{s['key']}={_quote(current.get(s['key'], s['default']))}")
    extras = [k for k in current if k not in _SCHEMA_BY_KEY]
    if extras:
        lines += ["", "# ---- 其他 ----"] + [f"{k}={_quote(current[k])}" for k in extras]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reload()


def _tr(text: str) -> str:
    from .i18n import tr
    return tr(text)


def _quote(v: str) -> str:
    v = str(v)
    if v == "" or all(c.isalnum() or c in "-_./:@?=&%+~,*" for c in v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fmt_jpy(v: int | float | None) -> str:
    if v is None:
        return "-"
    s = f"¥{int(v):,}"
    if JPY_TO_LOCAL_RATE > 0:
        s += f"（約 {LOCAL_CURRENCY} {v * JPY_TO_LOCAL_RATE:,.0f}）"
    return s


# 給型別檢查 / IDE 用的宣告，實際值由 reload() 填入
OPENAI_API_KEY: str
OPENAI_BASE_URL: str
OPENAI_MODEL_NAME: str
AI_IMAGE_MODE: str
AI_MAX_IMAGES: int
AI_RESPONSE_FORMAT_JSON: bool
AI_TIMEOUT_SECONDS: int
AI_DEBUG: bool
AI_PROXY_URL: str
AI_ENABLED: bool
REQUEST_DELAY_SECONDS: float
SCRAPE_PROXY_URL: str
MIN_INTERVAL_MINUTES: int
MAX_AI_PER_RUN: int
MARKET_CACHE_HOURS: int
LOCAL_CURRENCY: str
JPY_TO_LOCAL_RATE: float
SERVER_HOST: str
SERVER_PORT: int
WEB_USERNAME: str
WEB_PASSWORD: str
NTFY_TOPIC_URL: str
TELEGRAM_BOT_TOKEN: str
TELEGRAM_CHAT_ID: str
TELEGRAM_API_BASE_URL: str
DISCORD_WEBHOOK_URL: str
BARK_URL: str
WEBHOOK_URL: str
DB_FILE: Path
UI_LANGUAGE: str
LANG: str  # 實際使用的語言：zh-TW 或 zh-CN

reload()
_first_run_files()
