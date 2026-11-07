"""雅虎日拍撿漏監控 — 指令列入口。

    python main.py web                # 啟動 Web 管理介面 + 定時排程（推薦）
    python main.py run                # 所有啟用的任務各跑一次
    python main.py run -t "任務名稱"   # 只跑指定任務
    python main.py daemon             # 不開 Web，只在背景定時跑
    python main.py search 關鍵字       # 試搜尋，看看抓到什麼（不分析、不記錄）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from monitor import config, runner, yahoo  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_DIR / "monitor.log", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def cmd_run(task_name: str | None) -> None:
    tasks = runner.load_tasks()
    if not tasks:
        print("tasks.json 沒有任務。請先複製 tasks.example.json 為 tasks.json 並修改。")
        return
    for t in tasks:
        if task_name and t["name"] != task_name:
            continue
        if not task_name and not t.get("enabled"):
            continue
        await runner.run_task(t)


async def cmd_search(keyword: str, sort: str, closed: bool) -> None:
    task = {"keyword": keyword, "sort": sort}
    async with yahoo.make_client() as c:
        items, total = await yahoo.search(c, task, closed=closed)
        print(f"共 {total} 筆，顯示前 {len(items)} 筆：")
        for it in items:
            kind = "フリマ" if it.is_fleamarket else ("定額" if it.is_fixed_price else f"拍賣{it.bid_count}標")
            print(f"  ¥{it.price:>8,}  {kind:<6} {it.condition_name:<10} {it.title[:50]}\n            {it.url}")
        if not closed:
            m = await yahoo.market_price(c, task)
            print("近期成交行情：", m)


async def cmd_daemon() -> None:
    from monitor.scheduler import scheduler
    await scheduler.loop()


def main() -> None:
    ap = argparse.ArgumentParser(description="雅虎日拍撿漏監控")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("web", help="啟動 Web 介面 + 排程")
    r = sub.add_parser("run", help="立即執行任務一次")
    r.add_argument("-t", "--task", help="任務名稱")
    sub.add_parser("daemon", help="只跑排程，不開 Web")
    s = sub.add_parser("search", help="試搜尋")
    s.add_argument("keyword")
    s.add_argument("--sort", default="new", choices=["new", "end", "price"])
    s.add_argument("--closed", action="store_true", help="搜尋已結束的拍賣（成交價）")
    args = ap.parse_args()

    if args.cmd == "run":
        asyncio.run(cmd_run(args.task))
    elif args.cmd == "daemon":
        asyncio.run(cmd_daemon())
    elif args.cmd == "search":
        asyncio.run(cmd_search(args.keyword, args.sort, args.closed))
    else:
        import uvicorn
        print(f"Web 介面：http://{config.SERVER_HOST}:{config.SERVER_PORT}")
        uvicorn.run("monitor.web:app", host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
