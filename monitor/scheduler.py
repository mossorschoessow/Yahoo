"""定時排程：每個任務依 interval_minutes 週期執行，一次只跑一個任務，避免請求太密集。"""
from __future__ import annotations

import asyncio
import collections
import logging
import time

from . import runner, storage

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.running: str | None = None
        self.queue: collections.deque[str] = collections.deque()
        self.logs: collections.deque[str] = collections.deque(maxlen=500)
        self.next_run: dict[str, float] = {}
        self._stop = asyncio.Event()

    def log(self, task: str, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [{task}] {msg}"
        self.logs.append(line)
        log.info("[%s] %s", task, msg)

    async def run_one(self, task: dict) -> None:
        async with self.lock:
            self.running = task["name"]
            try:
                await runner.run_task(task, progress=lambda m: self.log(task["name"], m))
            except Exception as e:
                self.log(task["name"], f"❌ 失敗：{e}")
            finally:
                self.running = None
                self.next_run[task["name"]] = time.time() + task["interval_minutes"] * 60

    def trigger(self, name: str) -> None:
        if name not in self.queue and name != self.running:
            self.queue.append(name)

    async def loop(self) -> None:
        # 啟動時依上次執行時間決定下次時間，避免重啟就全部狂跑
        for t in runner.load_tasks():
            last = storage.last_run(t["name"])
            base = last["started_at"] if last else 0
            self.next_run.setdefault(t["name"], base + t["interval_minutes"] * 60)
        while not self._stop.is_set():
            tasks = {t["name"]: t for t in runner.load_tasks()}
            todo: str | None = None
            if self.queue:
                todo = self.queue.popleft()
            else:
                now = time.time()
                due = [n for n, t in tasks.items() if t.get("enabled") and self.next_run.get(n, 0) <= now]
                if due:
                    todo = min(due, key=lambda n: self.next_run.get(n, 0))
            if todo and todo in tasks:
                await self.run_one(tasks[todo])
            else:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    def stop(self) -> None:
        self._stop.set()


scheduler = Scheduler()
