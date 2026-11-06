"""定時排程：每個任務依 interval_minutes 週期執行。

多個任務可以同時執行（上限 MAX_CONCURRENT_TASKS），超過上限的任務排隊等待。
同一個任務不會同時跑兩份。
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time

from . import config, runner, storage
from .i18n import tr

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self.running: set[str] = set()
        self.queue: collections.deque[str] = collections.deque()
        self.logs: collections.deque[str] = collections.deque(maxlen=1000)
        self.next_run: dict[str, float] = {}
        self._jobs: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def log(self, task: str, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [{task}] {msg}"
        self.logs.append(line)
        log.info("[%s] %s", task, msg)

    @staticmethod
    def limit() -> int:
        return max(1, int(getattr(config, "MAX_CONCURRENT_TASKS", 3) or 1))

    async def run_one(self, task: dict) -> None:
        name = task["name"]
        try:
            await runner.run_task(task, progress=lambda m: self.log(name, m))
        except Exception as e:
            self.log(name, tr("❌ 失敗：{e}").format(e=e))
        finally:
            self.running.discard(name)
            self._jobs.pop(name, None)
            self.next_run[name] = time.time() + task["interval_minutes"] * 60
            self._poke()

    def _start(self, task: dict) -> None:
        name = task["name"]
        self.running.add(name)
        self._jobs[name] = asyncio.create_task(self.run_one(task), name=f"task:{name}")

    def _poke(self) -> None:
        """叫醒排程迴圈（可以從其他執行緒呼叫）。"""
        if self._loop is None or self._wake is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:  # 迴圈已關閉
            pass

    def trigger(self, name: str) -> None:
        if name not in self.queue and name not in self.running:
            self.queue.append(name)
            self._poke()

    def _pick(self, tasks: dict[str, dict]) -> list[str]:
        """依優先順序（手動觸發 → 到期最久）挑出這一輪要啟動的任務。"""
        free = self.limit() - len(self.running)
        picked: list[str] = []
        # 手動觸發的任務：超過上限的留在佇列裡繼續排隊
        for name in list(self.queue):
            if name not in tasks:
                self.queue.remove(name)
            elif name in self.running:
                continue
            elif len(picked) < free:
                self.queue.remove(name)
                picked.append(name)
        now = time.time()
        due = sorted(
            (n for n, t in tasks.items()
             if t.get("enabled") and n not in self.running and n not in picked and n not in self.queue
             and self.next_run.get(n, 0) <= now),
            key=lambda n: self.next_run.get(n, 0),
        )
        picked += due[: max(0, free - len(picked))]
        return picked

    async def loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        # 啟動時依上次執行時間決定下次時間，避免重啟就全部狂跑
        for t in runner.load_tasks():
            last = storage.last_run(t["name"])
            base = last["started_at"] if last else 0
            self.next_run.setdefault(t["name"], base + t["interval_minutes"] * 60)
        while not self._stop.is_set():
            self._wake.clear()
            tasks = {t["name"]: t for t in runner.load_tasks()}
            for i, name in enumerate(self._pick(tasks)):
                if i:  # 同時啟動多個任務時錯開一點，不要同一秒一起打日拍
                    await asyncio.sleep(min(config.REQUEST_DELAY_SECONDS, 3))
                if name not in self.running:
                    self._start(tasks[name])
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
        self._poke()
        for job in list(self._jobs.values()):
            job.cancel()


scheduler = Scheduler()
