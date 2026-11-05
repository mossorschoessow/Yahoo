"""推播通知：ntfy / Telegram / Discord / Bark / 通用 Webhook。設定任一即可。"""
from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import quote

import httpx

from . import config
from .i18n import tr

log = logging.getLogger(__name__)


def channels() -> list[str]:
    out = []
    if config.NTFY_TOPIC_URL:
        out.append("ntfy")
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        out.append("telegram")
    if config.DISCORD_WEBHOOK_URL:
        out.append("discord")
    if config.BARK_URL:
        out.append("bark")
    if config.WEBHOOK_URL:
        out.append("webhook")
    return out


def build_message(task: dict, item: dict, analysis: dict, market: dict | None) -> tuple[str, str]:
    level = analysis.get("deal_level") or ""
    title = f"🎯 {level}｜{task['name']}｜{config.fmt_jpy(item['price'])}"
    kind = "フリマ" if item.get("is_fleamarket") else (tr("定額") if item.get("is_fixed_price") else tr("拍賣・{n} 人出價").format(n=item.get("bid_count", 0)))
    lines = [
        analysis.get("title_zh") or item["title"],
        tr("價格：{p}（{k}）").format(p=config.fmt_jpy(item["price"]), k=kind),
    ]
    if market and market.get("median"):
        lines.append(tr("近期成交中位數：{m}（現價為 {r:.0f}%）").format(m=config.fmt_jpy(market["median"]), r=item["price"] / market["median"] * 100))
    if analysis.get("estimated_final_price_jpy"):
        lines.append(tr("AI 預估成交：") + config.fmt_jpy(analysis["estimated_final_price_jpy"]))
    lines.append(tr("成色：") + (item.get("condition_name") or "-"))
    if item.get("end_time") and not item.get("is_fleamarket"):
        lines.append(tr("結束：{t}（日本時間）").format(t=item["end_time"].replace("T", " ")[:16]))
    lines.append(tr("AI 評分：") + str(analysis.get("score", "-")))
    lines.append(tr("理由：") + str(analysis.get("reason", "")))
    if analysis.get("risk_tags"):
        lines.append(tr("風險：") + "、".join(analysis["risk_tags"]))
    lines.append(item["url"])
    return title, "\n".join(lines)


async def _ntfy(http: httpx.AsyncClient, title: str, body: str, item: dict) -> None:
    r = await http.post(
        config.NTFY_TOPIC_URL,
        content=body.encode("utf-8"),
        headers={
            # HTTP header 只能放 latin-1，用 RFC 2047 編碼標題
            "Title": "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?=",
            "Click": item["url"],
            **({"Attach": item["image"]} if item.get("image") else {}),
            "Priority": "high",
            "Tags": "moneybag",
        },
    )
    r.raise_for_status()


async def _telegram(http: httpx.AsyncClient, title: str, body: str, item: dict) -> None:
    base = f"{config.TELEGRAM_API_BASE_URL.rstrip('/')}/bot{config.TELEGRAM_BOT_TOKEN}"
    text = f"{title}\n\n{body}"
    photo = (item.get("images") or [item.get("image")])[0]
    if photo and len(text) <= 1000:
        r = await http.post(f"{base}/sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "photo": photo, "caption": text})
        if r.status_code == 200:
            return
    r = await http.post(f"{base}/sendMessage", data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text})
    r.raise_for_status()


async def _discord(http: httpx.AsyncClient, title: str, body: str, item: dict) -> None:
    embed: dict[str, Any] = {"title": title[:256], "description": body[:4000], "url": item["url"], "color": 0xFF0033}
    if item.get("image"):
        embed["thumbnail"] = {"url": item["image"]}
    r = await http.post(config.DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
    r.raise_for_status()


async def _bark(http: httpx.AsyncClient, title: str, body: str, item: dict) -> None:
    url = f"{config.BARK_URL.rstrip('/')}/{quote(title)}/{quote(body)}"
    r = await http.get(url, params={"url": item["url"], "icon": item.get("image") or "", "group": "yahoo-auction"})
    r.raise_for_status()


async def _webhook(http: httpx.AsyncClient, title: str, body: str, item: dict, analysis: dict) -> None:
    r = await http.post(config.WEBHOOK_URL, json={"title": title, "content": body, "item": item, "analysis": analysis})
    r.raise_for_status()


async def _send_all(title: str, body: str, item: dict, analysis: dict) -> dict[str, str | None]:
    """回傳 {管道: None（成功）或錯誤訊息}。"""
    out: dict[str, str | None] = {}
    async with httpx.AsyncClient(timeout=20) as http:
        for ch in channels():
            try:
                if ch == "ntfy":
                    await _ntfy(http, title, body, item)
                elif ch == "telegram":
                    await _telegram(http, title, body, item)
                elif ch == "discord":
                    await _discord(http, title, body, item)
                elif ch == "bark":
                    await _bark(http, title, body, item)
                elif ch == "webhook":
                    await _webhook(http, title, body, item, analysis)
                out[ch] = None
            except Exception as e:
                log.warning("%s 通知失敗：%s", ch, e)
                out[ch] = str(e) or e.__class__.__name__
    return out


async def send(task: dict, item: dict, analysis: dict, market: dict | None) -> list[str]:
    title, body = build_message(task, item, analysis, market)
    res = await _send_all(title, body, item, analysis)
    if not res:
        log.info(tr("（沒有設定通知管道，僅記錄）") + "%s\n%s", title, body)
    return [ch for ch, err in res.items() if err is None]


async def send_test() -> dict[str, str | None]:
    item = {"id": "test", "title": tr("測試通知"), "price": 12345, "url": "https://auctions.yahoo.co.jp/", "image": "", "images": []}
    return await _send_all(f"🔔 {tr(config.APP_TITLE)} " + tr("測試通知"), tr("如果你看到這則訊息，代表通知設定成功。"), item, {})
