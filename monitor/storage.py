"""SQLite 儲存：已看過的商品、分析結果、行情快取、執行紀錄。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    task TEXT NOT NULL,
    item_id TEXT NOT NULL,
    first_seen REAL NOT NULL,
    PRIMARY KEY (task, item_id)
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT,
    price INTEGER,
    url TEXT,
    image TEXT,
    recommended INTEGER,
    score INTEGER,
    deal_level TEXT,
    reason TEXT,
    item_json TEXT,
    analysis_json TEXT,
    market_json TEXT,
    notified INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE (task, item_id)
);
CREATE INDEX IF NOT EXISTS idx_results_task ON results(task, created_at DESC);
CREATE TABLE IF NOT EXISTS market_cache (
    task TEXT PRIMARY KEY,
    data TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT,
    message TEXT,
    scanned INTEGER DEFAULT 0,
    analyzed INTEGER DEFAULT 0,
    hits INTEGER DEFAULT 0
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def _exec(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur


def _query(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


# --- seen ---
def seen_ids(task: str) -> set[str]:
    return {r["item_id"] for r in _query("SELECT item_id FROM seen WHERE task=?", (task,))}


def mark_seen(task: str, item_id: str) -> None:
    _exec("INSERT OR IGNORE INTO seen(task, item_id, first_seen) VALUES (?,?,?)", (task, item_id, time.time()))


# --- results ---
def save_result(task: str, item: dict, analysis: dict, market: dict | None) -> None:
    _exec(
        """INSERT OR REPLACE INTO results
           (task, item_id, title, price, url, image, recommended, score, deal_level, reason,
            item_json, analysis_json, market_json, notified, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
        (
            task, item["id"], item["title"], item["price"], item["url"], item.get("image"),
            1 if analysis.get("is_recommended") else 0,
            int(analysis.get("score") or 0),
            analysis.get("deal_level"),
            analysis.get("reason"),
            json.dumps(item, ensure_ascii=False),
            json.dumps(analysis, ensure_ascii=False),
            json.dumps(market, ensure_ascii=False) if market else None,
            time.time(),
        ),
    )


def mark_notified(task: str, item_id: str) -> None:
    _exec("UPDATE results SET notified=1 WHERE task=? AND item_id=?", (task, item_id))


def list_results(task: str | None = None, recommended_only: bool = False, limit: int = 100, offset: int = 0) -> list[dict]:
    where, args = [], []
    if task:
        where.append("task=?")
        args.append(task)
    if recommended_only:
        where.append("recommended=1")
    sql = "SELECT * FROM results"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    rows = _query(sql, tuple(args) + (limit, offset))
    for r in rows:
        for k in ("item_json", "analysis_json", "market_json"):
            r[k.replace("_json", "")] = json.loads(r.pop(k)) if r.get(k) else None
    return rows


def delete_task_data(task: str) -> None:
    for t in ("seen", "results", "market_cache", "runs"):
        _exec(f"DELETE FROM {t} WHERE task=?", (task,))


# --- market cache ---
def get_market(task: str, max_age_hours: float) -> dict | None:
    rows = _query("SELECT data, updated_at FROM market_cache WHERE task=?", (task,))
    if rows and time.time() - rows[0]["updated_at"] < max_age_hours * 3600 and rows[0]["data"]:
        return json.loads(rows[0]["data"])
    return None


def set_market(task: str, data: dict | None) -> None:
    _exec(
        "INSERT OR REPLACE INTO market_cache(task, data, updated_at) VALUES (?,?,?)",
        (task, json.dumps(data, ensure_ascii=False) if data else None, time.time()),
    )


# --- runs ---
def start_run(task: str) -> int:
    return _exec("INSERT INTO runs(task, started_at, status) VALUES (?,?,'running')", (task, time.time())).lastrowid


def finish_run(run_id: int, status: str, message: str = "", scanned: int = 0, analyzed: int = 0, hits: int = 0) -> None:
    _exec(
        "UPDATE runs SET finished_at=?, status=?, message=?, scanned=?, analyzed=?, hits=? WHERE id=?",
        (time.time(), status, message, scanned, analyzed, hits, run_id),
    )


def recent_runs(limit: int = 30) -> list[dict]:
    return _query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def last_run(task: str) -> dict | None:
    rows = _query("SELECT * FROM runs WHERE task=? ORDER BY id DESC LIMIT 1", (task,))
    return rows[0] if rows else None
