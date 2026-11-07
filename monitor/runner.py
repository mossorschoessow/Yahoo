"""單一監控任務的執行流程。

搜尋 → 本地預篩 → 去重 → 抓商品頁 → 取成交行情 → AI 分析 → 存檔 → 推播
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from . import ai, config, notify, storage, yahoo
from .i18n import tr

log = logging.getLogger(__name__)

TASK_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "description": "",
    "min_price": None,
    "max_price": None,
    "condition": "any",          # any | new | used
    "listing_type": "any",       # any | auction | buynow
    "sort": "new",               # new | end | price
    "max_pages": 1,
    "exclude_words": ["ジャンク", "部品取り", "箱のみ", "空箱", "写真のみ", "画像のみ"],
    "require_words": [],
    "individual_only": False,    # 只看個人賣家（排除商店）
    "include_fleamarket": True,  # 是否包含 PayPayフリマ
    "min_seller_rating": None,   # 好評率下限（%）
    "max_bids": None,            # 出價數上限（太多人搶通常撿不到漏）
    "max_end_hours": None,       # 只看 N 小時內結束的拍賣
    "interval_minutes": 20,
    "ai_enabled": True,
    "notify_min_score": 70,
    "deal_ratio": 0.7,           # 規則模式：現價 ≤ 中位數 × 此比例才通知
    "category_id": None,
}


MAX_PAGES = 5  # 每次最多掃描幾頁（每頁 50 筆）


def normalize_task(t: dict[str, Any]) -> dict[str, Any]:
    out = {**TASK_DEFAULTS, **t}
    if not out.get("name"):
        out["name"] = out.get("keyword", "未命名任務")
    out["interval_minutes"] = max(config.MIN_INTERVAL_MINUTES, int(out.get("interval_minutes") or 20))
    out["max_pages"] = max(1, min(MAX_PAGES, int(out.get("max_pages") or 1)))
    return out


def load_tasks() -> list[dict[str, Any]]:
    if not config.TASKS_FILE.exists():
        return []
    raw = json.loads(config.TASKS_FILE.read_text(encoding="utf-8"))
    return [normalize_task(t) for t in raw]


def save_tasks(tasks: list[dict[str, Any]]) -> None:
    names = [t.get("name") for t in tasks]
    if len(names) != len(set(names)):
        raise ValueError(tr("任務名稱不能重複"))
    config.TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def prefilter(item: yahoo.Item, task: dict[str, Any]) -> str | None:
    """回傳 None 代表通過；否則回傳被排除的原因。"""
    if not yahoo._passes_title_filter(item.title, task):
        return "標題含排除字 / 缺必要字"
    if task.get("max_price") and item.price > int(task["max_price"]):
        return "超過價格上限"
    if task.get("min_price") and item.price < int(task["min_price"]):
        return "低於價格下限"
    if task.get("individual_only") and item.seller_is_store:
        return "商店賣家"
    if not task.get("include_fleamarket", True) and item.is_fleamarket:
        return "フリマ商品"
    if task.get("min_seller_rating") and item.seller_good_rating is not None \
            and item.seller_good_rating < float(task["min_seller_rating"]):
        return "賣家好評率太低"
    if task.get("max_bids") is not None and item.bid_count > int(task["max_bids"]):
        return "出價人數太多"
    if task.get("max_end_hours") and not item.is_fixed_price:
        try:
            end = datetime.fromisoformat(item.end_time)
            hours = (end - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours > float(task["max_end_hours"]):
                return "結束時間太遠"
        except ValueError:
            pass
    return None


async def get_market(client, task: dict[str, Any]) -> dict | None:
    cached = storage.get_market(task["name"], config.MARKET_CACHE_HOURS)
    if cached:
        return cached
    try:
        m = await yahoo.market_price(client, task)
    except Exception as e:
        log.warning("[%s] 取得成交行情失敗：%s", task["name"], e)
        return None
    storage.set_market(task["name"], m)
    return m


async def run_task(task: dict[str, Any], *, progress=None) -> dict[str, Any]:
    task = normalize_task(task)
    name = task["name"]
    say = progress or (lambda msg: log.info("[%s] %s", name, msg))
    run_id = storage.start_run(name)
    scanned = analyzed = hits = 0
    try:
        seen = storage.seen_ids(name)
        candidates: list[yahoo.Item] = []
        async with yahoo.make_client() as client:
            for page in range(1, task["max_pages"] + 1):
                items, total = await yahoo.search(client, task, page)
                scanned += len(items)
                say(tr("第 {page} 頁：{n} 筆（總共 {total} 筆）").format(page=page, n=len(items), total=total))
                for it in items:
                    if it.id in seen:
                        continue
                    reason = prefilter(it, task)
                    if reason:
                        storage.mark_seen(name, it.id)  # 不符合條件的直接記下，不再處理
                        continue
                    candidates.append(it)
                if len(items) < 50:
                    break

            if not candidates:
                say(tr("沒有新商品"))
                storage.finish_run(run_id, "ok", tr("沒有新商品"), scanned)
                return {"scanned": scanned, "analyzed": 0, "hits": 0}

            batch = candidates[: config.MAX_AI_PER_RUN]
            say(tr("新商品 {a} 筆，本次處理 {b} 筆").format(a=len(candidates), b=len(batch)))
            market = await get_market(client, task)
            if market:
                say(tr("近期成交行情：中位數 ¥{m:,}（樣本 {n}）").format(m=market["median"], n=market["sample_size"]))

            use_ai = task.get("ai_enabled", True) and config.AI_ENABLED
            if task.get("ai_enabled", True) and not config.AI_ENABLED:
                say(tr("⚠ 未設定 OPENAI_API_KEY，改用規則模式（只比價格）"))

            for it in batch:
                try:
                    await yahoo.fetch_detail(client, it)
                except yahoo.YahooBlocked:
                    raise  # 被日拍拒絕：剩下的商品留到下次，不要標記為已看過
                except yahoo.YahooError as e:
                    say(tr("商品頁讀取失敗 {id}：{e}").format(id=it.id, e=e))
                    storage.mark_seen(name, it.id)
                    continue
                item = it.to_dict()
                try:
                    analysis = await ai.analyze(item, task, market, client) if use_ai else ai.rule_based(item, task, market)
                except Exception as e:
                    say(tr("AI 分析失敗 {id}：{e}（下次再試）").format(id=it.id, e=e))
                    continue
                analyzed += 1
                storage.save_result(name, item, analysis, market)
                storage.mark_seen(name, it.id)
                ok = analysis.get("is_recommended") and int(analysis.get("score") or 0) >= int(task["notify_min_score"] if use_ai else 0)
                tag = "✅" if ok else "  "
                say(f"{tag} {analysis.get('score', 0):>3} " + tr("分") + f" ¥{it.price:,} {it.title[:40]}")
                if ok:
                    hits += 1
                    await notify.send(task, item, analysis, market)
                    storage.mark_notified(name, it.id)

        msg = tr("掃描 {a}、分析 {b}、推薦 {c}").format(a=scanned, b=analyzed, c=hits)
        if len(candidates) > len(batch):
            msg += tr("（還有 {n} 筆留到下次）").format(n=len(candidates) - len(batch))
        storage.finish_run(run_id, "ok", msg, scanned, analyzed, hits)
        say(tr("完成：") + msg)
        return {"scanned": scanned, "analyzed": analyzed, "hits": hits}
    except yahoo.YahooBlocked as e:
        log.warning("[%s] %s", name, e)
        storage.finish_run(run_id, "error", str(e), scanned, analyzed, hits)
        raise
    except Exception as e:
        log.exception("[%s] 任務失敗", name)
        storage.finish_run(run_id, "error", str(e), scanned, analyzed, hits)
        raise
