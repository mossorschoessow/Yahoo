"""用 OpenAI 相容的多模態模型分析商品。"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from . import config

log = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def reset_client() -> None:
    """設定變更後呼叫，下次請求會用新的 API Key / 位址。"""
    global _client
    _client = None


async def test_connection() -> str:
    """設定頁的「測試 AI」：送一個純文字小請求。"""
    resp = await client().chat.completions.create(
        model=config.OPENAI_MODEL_NAME,
        messages=[{"role": "user", "content": "請只回覆這個 JSON：{\"ok\": true}"}],
        max_tokens=20,
    )
    return (resp.choices[0].message.content or "").strip()


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        http_client = httpx.AsyncClient(proxy=config.AI_PROXY_URL or None, timeout=config.AI_TIMEOUT_SECONDS)
        _client = AsyncOpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            http_client=http_client,
        )
    return _client


def load_prompt(task: dict[str, Any]) -> str:
    base = (config.PROMPTS_DIR / "base_prompt.txt").read_text(encoding="utf-8")
    criteria = (task.get("description") or "").strip()
    crit_file = task.get("criteria_file")
    if crit_file:
        p = config.PROMPTS_DIR.parent / crit_file
        if p.exists():
            criteria = (criteria + "\n\n" + p.read_text(encoding="utf-8")).strip()
    if not criteria:
        criteria = f"我在找「{task['keyword']}」，希望以低於行情的價格買到狀況良好的商品。"
    return base.replace("{{CRITERIA}}", criteria)


async def _image_payloads(http: httpx.AsyncClient, urls: list[str]) -> list[str]:
    urls = [u for u in urls if u.startswith("http")][: config.AI_MAX_IMAGES]
    if config.AI_IMAGE_MODE == "none" or not urls:
        return []
    if config.AI_IMAGE_MODE == "url":
        return urls

    async def one(u: str) -> str | None:
        try:
            r = await http.get(u)
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
            return f"data:{mime};base64,{base64.b64encode(r.content).decode()}"
        except Exception as e:
            log.warning("圖片下載失敗 %s：%s", u, e)
            return None

    res = await asyncio.gather(*(one(u) for u in urls))
    return [x for x in res if x]


def _item_for_ai(item: dict[str, Any], market: dict | None) -> dict[str, Any]:
    keys = [
        "id", "title", "price", "buy_now_price", "bid_count", "watch_count", "is_fixed_price",
        "is_fleamarket", "condition_name", "end_time", "free_shipping", "shipping_fee",
        "seller_is_store", "seller_good_rating", "seller_rating_count", "category", "description", "detail",
    ]
    data = {k: item.get(k) for k in keys}
    data["listing_type"] = "PayPayフリマ（定額）" if item.get("is_fleamarket") else ("定額 / 即決" if item.get("is_fixed_price") else "拍賣")
    if data.get("description") and len(data["description"]) > 4000:
        data["description"] = data["description"][:4000] + "…（以下省略）"
    if market:
        data["market"] = {"說明": "近期同關鍵字已結束拍賣的成交價統計（日圓）", **market}
        if market.get("median"):
            data["market"]["現價相對中位數"] = f"{item['price'] / market['median'] * 100:.0f}%"
    return data


def parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict) or "is_recommended" not in data:
        raise ValueError("AI 回傳缺少 is_recommended 欄位")
    data["is_recommended"] = bool(data["is_recommended"]) if not isinstance(data["is_recommended"], str) \
        else data["is_recommended"].lower() == "true"
    try:
        data["score"] = max(0, min(100, int(data.get("score") or 0)))
    except (TypeError, ValueError):
        data["score"] = 0
    return data


async def analyze(item: dict[str, Any], task: dict[str, Any], market: dict | None, http: httpx.AsyncClient) -> dict[str, Any]:
    prompt = load_prompt(task)
    product_json = json.dumps(_item_for_ai(item, market), ensure_ascii=False, indent=1)
    text = f"以下是要分析的商品 JSON：\n```json\n{product_json}\n```\n\n{prompt}"
    images = await _image_payloads(http, item.get("images") or [item.get("image", "")])
    if not images:
        text += "\n\n補充：本次沒有提供圖片，請只根據文字判斷，不要推測圖片內容。"
    content: Any = [{"type": "image_url", "image_url": {"url": u}} for u in images] + [{"type": "text", "text": text}] \
        if images else text

    kwargs: dict[str, Any] = {
        "model": config.OPENAI_MODEL_NAME,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
    }
    if config.AI_RESPONSE_FORMAT_JSON:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client().chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content or ""
            if config.AI_DEBUG:
                log.info("AI 原始回覆：%s", raw)
            return parse_json_response(raw)
        except Exception as e:
            last_err = e
            msg = str(e)
            # 有些模型不支援 response_format，自動退回
            if "response_format" in msg and "response_format" in kwargs:
                kwargs.pop("response_format")
                continue
            log.warning("AI 分析失敗（第 %d 次）：%s", attempt + 1, e)
            await asyncio.sleep(3 * (attempt + 1))
    raise RuntimeError(f"AI 分析失敗：{last_err}")


def rule_based(item: dict[str, Any], task: dict[str, Any], market: dict | None) -> dict[str, Any]:
    """未設定 AI 時的退路：單純用「現價 vs 成交中位數」判斷。"""
    ratio = float(task.get("deal_ratio") or 0.7)
    if not market or not market.get("median"):
        return {"is_recommended": False, "score": 0, "deal_level": "無行情資料",
                "reason": "未設定 AI，且找不到成交行情，無法判斷", "risk_tags": []}
    r = item["price"] / market["median"]
    # 拍賣還會漲，只對定額 / フリマ 或快結束的拍賣有意義
    is_final_price = item.get("is_fixed_price") or item.get("is_fleamarket") or (item.get("left_seconds") or 1e9) < 3600
    rec = r <= ratio and is_final_price
    level = "超值撿漏" if r <= ratio * 0.8 else "划算" if r <= ratio else "行情價" if r <= 1.1 else "偏貴"
    return {
        "is_recommended": bool(rec),
        "score": max(0, min(100, int((1.2 - r) * 100))) if rec else 0,
        "deal_level": level,
        "reason": f"現價為近期成交中位數的 {r * 100:.0f}%（規則模式，未經 AI 檢查商品狀況，請自行確認）",
        "risk_tags": [] if is_final_price else ["拍賣中，價格可能再上漲"],
    }
