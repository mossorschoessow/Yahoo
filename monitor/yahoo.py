"""雅虎日拍（ヤフオク!）與 PayPayフリマ 的搜尋 / 商品頁解析。

日拍頁面是 Next.js，伺服器回傳的 HTML 內嵌 __NEXT_DATA__ JSON，
所以只要一般 HTTP 請求就能拿到完整資料，不需要瀏覽器或登入。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import statistics
import time
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlencode

import httpx

from . import config
from .i18n import tr

log = logging.getLogger(__name__)

SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"
CLOSED_SEARCH_URL = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"
AUCTION_ITEM_URL = "https://auctions.yahoo.co.jp/jp/auction/{id}"
FLEA_ITEM_URL = "https://paypayfleamarket.yahoo.co.jp/item/{id}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

CONDITION_NAMES = {
    "NEW": "新品",
    "USED10": "未使用に近い",
    "USED20": "目立った傷や汚れなし",
    "USED40": "やや傷や汚れあり",
    "USED60": "傷や汚れあり",
    "USED80": "全体的に状態が悪い",
    "JUNK": "ジャンク品",
}

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class YahooError(RuntimeError):
    pass


class YahooBlocked(YahooError):
    """日拍拒絕存取（403 / 429 / 503）。所有任務一起暫停，不要繼續打。"""


class YahooNetworkError(YahooError):
    """連不上日拍（網路 / 代理問題）。reason 是不含建議的原因說明。"""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------- 請求節流
# 所有任務共用同一個節奏：不管同時跑幾個任務，對日拍的總請求頻率都是
# 「每 REQUEST_DELAY_SECONDS 秒最多一次」，並加上隨機間隔，避免固定頻率的連續請求。
_pace: dict[str, Any] = {"loop": None, "lock": None, "next": 0.0}
# 被拒絕後的暫停時間：30 → 60 → 120 分鐘，成功一次就重置
_block: dict[str, float] = {"until": 0.0, "strikes": 0}
BLOCK_MINUTES = (30, 60, 120)


def _pace_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _pace["loop"] is not loop:  # 每個事件迴圈各用一把鎖（CLI / 測試會建立新的迴圈）
        _pace.update(loop=loop, lock=asyncio.Lock())
    return _pace["lock"]


async def _wait_turn() -> None:
    async with _pace_lock():
        wait = _pace["next"] - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _pace["next"] = time.monotonic() + config.REQUEST_DELAY_SECONDS * random.uniform(0.8, 1.6)


def blocked_until() -> float:
    """日拍限制中時回傳解除的時間（time.time()），沒有限制回傳 0。"""
    return _block["until"] if _block["until"] > time.time() else 0.0


def _blocked_error() -> YahooBlocked:
    left = max(1, round((_block["until"] - time.time()) / 60))
    return YahooBlocked(tr("日拍暫時拒絕存取，所有任務暫停，約 {m} 分鐘後自動恢復").format(m=left))


def _on_blocked(code: int) -> YahooBlocked:
    n = int(_block["strikes"])
    minutes = BLOCK_MINUTES[min(n, len(BLOCK_MINUTES) - 1)]
    _block.update(until=time.time() + minutes * 60, strikes=n + 1)
    log.warning("日拍回應 HTTP %s，暫停 %s 分鐘", code, minutes)
    return YahooBlocked(tr("被日拍拒絕（HTTP {code}），所有任務暫停 {m} 分鐘").format(code=code, m=minutes))


@dataclass
class Item:
    id: str
    title: str
    price: int
    url: str
    image: str = ""
    buy_now_price: int | None = None
    bid_count: int = 0
    watch_count: int = 0
    is_fixed_price: bool = False
    is_fleamarket: bool = False
    condition: str = ""
    condition_name: str = ""
    end_time: str = ""
    start_time: str = ""
    left_seconds: float | None = None
    free_shipping: bool = False
    shipping_fee: int | None = None
    prefecture_code: str = ""
    seller_id: str = ""
    seller_is_store: bool = False
    seller_good_rating: float | None = None  # 百分比
    category: str = ""
    # 以下由商品頁補齊
    description: str = ""
    images: list[str] = field(default_factory=list)
    seller_rating_count: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_next_data(html: str) -> dict[str, Any]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise YahooError(tr("頁面中找不到 __NEXT_DATA__，日拍頁面結構可能改版了"))
    return json.loads(m.group(1))


def _initial_state(data: dict[str, Any]) -> dict[str, Any]:
    props = data.get("props", {})
    return props.get("pageProps", {}).get("initialState") or props.get("initialState") or {}


def _pct(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return None


def make_client(proxy: str | None = None, timeout: float = 30) -> httpx.AsyncClient:
    proxy = proxy or config.SCRAPE_PROXY_URL or None
    client = httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True, proxy=proxy)
    client.yam_proxy = proxy  # 記下這個連線用的代理，錯誤訊息要用
    return client


def describe_error(e: BaseException) -> str:
    """把網路例外轉成看得懂的說明（httpx 的逾時例外常常沒有訊息）。"""
    if isinstance(e, httpx.ProxyError):
        kind = tr("代理連線失敗，請檢查「日拍代理」")
    elif isinstance(e, httpx.ConnectTimeout):
        kind = tr("連線逾時，連不上日拍")
    elif isinstance(e, httpx.ConnectError):
        kind = tr("連不上日拍")
    elif isinstance(e, httpx.TimeoutException):
        kind = tr("日拍回應逾時")
    else:
        kind = type(e).__name__
    detail = str(e).strip()
    return f"{kind}（{detail}）" if detail and detail != kind else kind


def _is_network_error(e: BaseException | None) -> bool:
    return isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.ProxyError))


async def _get(client: httpx.AsyncClient, url: str, *, retries: int = 2) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        if blocked_until():
            raise _blocked_error()
        await _wait_turn()
        try:
            r = await client.get(url)
            if r.status_code == 404:
                raise YahooError(f"404 Not Found: {url}")
            if r.status_code in (403, 429, 503):
                raise _on_blocked(r.status_code)
            r.raise_for_status()
            host = r.url.host or ""
            if not host.endswith("yahoo.co.jp"):
                # 被轉到別的網站：常見原因是地區限制（例如歐洲無法使用日本雅虎）或網路被攔截
                raise YahooError(tr("被導向到 {host}，不是日拍頁面：可能是所在地區無法使用日本雅虎，或網路 / 代理攔截了").format(host=host))
            _block["strikes"] = 0
            return r.text
        except YahooError:
            raise
        except Exception as e:  # 網路錯誤：等久一點再重試
            last = e
            log.warning("日拍請求失敗（第 %s 次）%s：%s", attempt + 1, url, describe_error(e))
            if attempt < retries:
                await asyncio.sleep(10 * (attempt + 1))
    reason = describe_error(last) if last else "?"
    msg = tr("請求失敗：{err}").format(err=reason)
    if not _is_network_error(last):
        raise YahooError(msg)
    proxy = getattr(client, "yam_proxy", None)
    if proxy:
        msg += tr("。目前透過代理 {p} 連線：請確認代理軟體有開、位址和連接埠正確").format(p=proxy)
    else:
        msg += tr("。請檢查網路；如果要開 VPN / 代理才能用 AI，「設定 → 爬取 → 日拍代理」也要填同一個代理")
    raise YahooNetworkError(msg, reason)


async def check_connection() -> dict[str, Any]:
    """設定頁的「測試日拍連線」：實際搜尋一次，回報哪個環節有問題。"""
    task = {"keyword": "Nintendo Switch", "sort": "new"}
    if blocked_until():
        return {"ok": False, "message": str(_blocked_error())}
    via = config.SCRAPE_PROXY_URL
    try:
        async with make_client(timeout=15) as c:
            items, total = await search(c, task, 1, retries=0)
    except YahooError as e:
        msg = tr("❌ 搜尋失敗：") + str(e)
        # 沒設日拍代理、但有 AI 代理：試試看走 AI 代理能不能連上
        ai_proxy = config.AI_PROXY_URL
        if not via and ai_proxy:
            try:
                async with make_client(ai_proxy, timeout=15) as c:
                    items, total = await search(c, task, 1, retries=0)
                if items:
                    msg += "\n" + tr("✅ 改走 AI 代理（{p}）可以連上日拍，請把「日拍代理」也填成這個").format(p=ai_proxy)
            except Exception as e2:
                why = e2.reason if isinstance(e2, YahooNetworkError) else str(e2) if isinstance(e2, YahooError) else describe_error(e2)
                msg += "\n" + tr("改走 AI 代理（{p}）也失敗：{why}").format(p=ai_proxy, why=why)
        return {"ok": False, "message": msg}
    if not items:
        return {"ok": False, "message": tr("⚠ 連得上日拍，但搜尋結果是空的（總共 {n} 筆），日拍頁面可能改版了").format(n=total)}
    route = tr("透過代理 {p}").format(p=via) if via else tr("直接連線")
    return {"ok": True, "message": tr("✅ 連線正常（{route}）：搜尋「{kw}」找到 {n} 筆，例如：{title}").format(
        route=route, kw=task["keyword"], n=total, title=items[0].title[:40])}


# --------------------------------------------------------------------------- 搜尋

def build_search_params(task: dict[str, Any], page: int, *, closed: bool = False) -> dict[str, Any]:
    params: dict[str, Any] = {"p": task["keyword"], "n": 50, "b": 1 + (page - 1) * 50, "ei": "utf-8"}
    if not closed:
        sort = task.get("sort", "new")
        if sort == "new":
            params.update(s1="new", o1="d")       # 新上架優先：撿漏最重要
        elif sort == "end":
            params.update(s1="end", o1="a")       # 快結束優先：適合低價狙擊
        elif sort == "price":
            params.update(s1="cbids", o1="a")     # 現價由低到高
        if task.get("min_price"):
            params["aucminprice"] = int(task["min_price"])
        if task.get("max_price"):
            params["aucmaxprice"] = int(task["max_price"])
        lt = task.get("listing_type", "any")
        if lt == "auction":
            params["fixed"] = 2
        elif lt == "buynow":
            params["fixed"] = 1
    cond = task.get("condition", "any")
    if cond == "new":
        params["istatus"] = 1
    elif cond == "used":
        params["istatus"] = 2
    if task.get("category_id"):
        params["auccat"] = task["category_id"]
    return params


def _parse_search_item(raw: dict[str, Any]) -> Item:
    aid = raw["auctionId"]
    is_flea = bool(raw.get("isFleamarketItem"))
    seller = raw.get("seller") or {}
    cond = raw.get("itemCondition") or ""
    fee = raw.get("currentPriceShippingFee")
    buy_now = raw.get("buyNowPrice") or None
    return Item(
        id=aid,
        title=raw.get("title", ""),
        price=int(raw.get("price") or 0),
        url=(FLEA_ITEM_URL if is_flea else AUCTION_ITEM_URL).format(id=aid),
        image=raw.get("imageUrl", ""),
        buy_now_price=int(buy_now) if buy_now else None,
        bid_count=int(raw.get("bidCount") or 0),
        watch_count=int(raw.get("watchCount") or 0),
        is_fixed_price=bool(raw.get("isFixedPrice")),
        is_fleamarket=is_flea,
        condition=cond,
        condition_name=CONDITION_NAMES.get(cond, cond),
        end_time=raw.get("endTime", ""),
        start_time=raw.get("startTime", ""),
        left_seconds=raw.get("leftTime"),
        free_shipping=bool(raw.get("isFreeShipping")),
        shipping_fee=int(fee) if isinstance(fee, (int, float)) and fee > 0 else None,
        prefecture_code=str(raw.get("prefectureCode") or ""),
        seller_id=seller.get("userId", ""),
        seller_is_store=bool(seller.get("isStore")),
        seller_good_rating=_pct(seller.get("goodRating")),
        category=(raw.get("category") or {}).get("name", ""),
    )


async def search(client: httpx.AsyncClient, task: dict[str, Any], page: int = 1, *, closed: bool = False,
                 retries: int = 2) -> tuple[list[Item], int]:
    base = CLOSED_SEARCH_URL if closed else SEARCH_URL
    url = f"{base}?{urlencode(build_search_params(task, page, closed=closed))}"
    html = await _get(client, url, retries=retries)
    state = _initial_state(_extract_next_data(html))
    listing = (state.get("search") or {}).get("items", {}).get("listing") or {}
    raws = listing.get("items") or []
    total = int(listing.get("totalResultsAvailable") or 0)
    items = []
    for raw in raws:
        try:
            items.append(_parse_search_item(raw))
        except Exception as e:  # 單筆解析失敗不影響整頁
            log.warning("解析搜尋結果失敗：%s", e)
    return items, total


# --------------------------------------------------------------------------- 商品頁

def _join_description(desc: Any) -> str:
    text = "\n".join(str(x) for x in desc if x) if isinstance(desc, list) else str(desc or "")
    text = re.sub(r"[ \t　]+", " ", text)  # 日拍 HTML 排版留下的大量空白
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


async def fetch_detail(client: httpx.AsyncClient, item: Item) -> Item:
    html = await _get(client, item.url)
    state = _initial_state(_extract_next_data(html))
    if item.is_fleamarket:
        d = ((state.get("itemsState") or {}).get("items") or {}).get("item") or {}
        if not d:
            raise YahooError("PayPayフリマ " + tr("商品頁找不到商品資料"))
        item.description = _join_description(d.get("description"))
        item.images = [i["url"] for i in d.get("images") or [] if isinstance(i, dict) and i.get("url")]
        cond = d.get("condition") or {}
        if cond.get("text"):
            item.condition_name = cond["text"]
        seller = d.get("seller") or {}
        rating = seller.get("rating") or {}
        item.seller_rating_count = rating.get("total")
        if rating.get("goodRatio") is not None:
            item.seller_good_rating = float(rating["goodRatio"])
        item.price = int(d.get("price") or item.price)
        item.detail = {
            k: d.get(k)
            for k in ("deliveryMethod", "deliverySchedule", "shippingPayer", "deliveryFee", "status", "brand", "category")
            if k in d
        }
        item.detail["seller_nickname"] = seller.get("nickname")
    else:
        d = ((state.get("item") or {}).get("detail") or {}).get("item") or {}
        if not d:
            raise YahooError(tr("日拍商品頁找不到商品資料"))
        item.description = _join_description(d.get("description"))
        item.images = [i["image"] for i in d.get("img") or [] if isinstance(i, dict) and i.get("image")]
        if d.get("conditionName"):
            item.condition_name = d["conditionName"]
        seller = d.get("seller") or {}
        rating = seller.get("rating") or {}
        item.seller_rating_count = rating.get("summary")
        if rating.get("goodRating") is not None:
            item.seller_good_rating = _pct(rating["goodRating"])
        if "isStore" in seller:
            item.seller_is_store = bool(seller["isStore"])
        # 商店賣家的 price 是未稅價，買家實付的是 taxinPrice
        item.price = int(d.get("taxinPrice") or d.get("price") or item.price)
        item.bid_count = int(d.get("bids") or item.bid_count)
        item.end_time = d.get("endTime") or item.end_time
        returnable = d.get("itemReturnable") or {}
        item.detail = {
            "稅後現價": d.get("taxinPrice"),
            "起標價": d.get("initPrice"),
            "出價人數": d.get("biddersNum"),
            "關注人數": d.get("watchListNum"),
            "運費負擔": {"winner": "得標者負擔", "seller": "賣家負擔（免運）"}.get(d.get("chargeForShipping"), d.get("chargeForShipping")),
            "可退貨": returnable.get("allowed"),
            "退貨說明": returnable.get("comment"),
            "提前結束": d.get("isEarlyClosing"),
            "自動延長": d.get("isAutomaticExtension"),
            "限制評價差的出價者": d.get("isBidderRestrictions"),
            "型錄商品名": d.get("productName"),
            "品牌": [b.get("name") for b in (d.get("brand") or {}).get("path", [])],
            "分類": " > ".join(c.get("name", "") for c in (d.get("category") or {}).get("path", [])[1:]),
            "已回答問答數": d.get("answeredQAndANum"),
        }
    if not item.images and item.image:
        item.images = [item.image]
    return item


# --------------------------------------------------------------------------- 落札相場

def _passes_title_filter(title: str, task: dict[str, Any]) -> bool:
    t = title.lower()
    if any(w and w.lower() in t for w in task.get("exclude_words") or []):
        return False
    req = task.get("require_words") or []
    return all(w.lower() in t for w in req if w)


async def market_price(client: httpx.AsyncClient, task: dict[str, Any]) -> dict[str, Any] | None:
    """以「已結束拍賣」(closedsearch) 估算近期成交行情。"""
    items, total = await search(client, task, 1, closed=True)
    # 低於 min_price 的成交多半是配件 / 零件，會拉低行情，預設排除
    floor = int(task.get("market_min_price") or task.get("min_price") or 1)
    prices = sorted(i.price for i in items if i.price >= floor and _passes_title_filter(i.title, task))
    if len(prices) < 3:
        return None
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
    return {
        "sample_size": len(prices),
        "median": int(statistics.median(prices)),
        "p25": int(q[0]),
        "p75": int(q[-1]),
        "min": prices[0],
        "max": prices[-1],
    }
