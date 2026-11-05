"""Web 管理介面（FastAPI）。"""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import config, notify, runner, storage
from .scheduler import scheduler

security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not config.WEB_PASSWORD:
        return  # 沒設密碼就不驗證（預設只綁 127.0.0.1）
    ok = creds and secrets.compare_digest(creds.username, config.WEB_USERNAME) \
        and secrets.compare_digest(creds.password, config.WEB_PASSWORD)
    if not ok:
        raise HTTPException(401, "需要登入", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    storage.conn()
    bg = asyncio.create_task(scheduler.loop())
    yield
    scheduler.stop()
    bg.cancel()


app = FastAPI(title="雅虎日拍撿漏監控", lifespan=lifespan, dependencies=[Depends(auth)])


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.BASE_DIR / "static" / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "ai_enabled": config.AI_ENABLED,
        "model": config.OPENAI_MODEL_NAME if config.AI_ENABLED else None,
        "notify_channels": notify.channels(),
        "running": scheduler.running,
        "queue": list(scheduler.queue),
        "next_run": {k: v for k, v in scheduler.next_run.items()},
        "now": time.time(),
        "jpy_rate": config.JPY_TO_LOCAL_RATE,
        "local_currency": config.LOCAL_CURRENCY,
    }


@app.get("/api/tasks")
def get_tasks() -> list[dict[str, Any]]:
    out = []
    for t in runner.load_tasks():
        t["last_run"] = storage.last_run(t["name"])
        out.append(t)
    return out


@app.put("/api/tasks")
def put_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    clean = []
    for t in tasks:
        t = {k: v for k, v in t.items() if k != "last_run"}
        if not (t.get("keyword") or "").strip():
            raise HTTPException(400, "每個任務都必須有關鍵字")
        t["name"] = (t.get("name") or t["keyword"]).strip()
        clean.append(t)
    try:
        runner.save_tasks(clean)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/tasks/{name}/run")
def run_now(name: str) -> dict[str, Any]:
    if name not in {t["name"] for t in runner.load_tasks()}:
        raise HTTPException(404, "找不到任務")
    scheduler.trigger(name)
    return {"ok": True}


@app.delete("/api/tasks/{name}/data")
def reset_task(name: str) -> dict[str, Any]:
    storage.delete_task_data(name)
    return {"ok": True}


@app.get("/api/results")
def results(task: str | None = None, recommended: bool = False, limit: int = 60, offset: int = 0) -> list[dict]:
    return storage.list_results(task, recommended, min(limit, 300), offset)


@app.get("/api/logs")
def logs() -> dict[str, Any]:
    return {"lines": list(scheduler.logs), "runs": storage.recent_runs(30)}
