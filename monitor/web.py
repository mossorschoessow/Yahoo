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

from . import ai, config, notify, runner, storage
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


app = FastAPI(title=config.APP_TITLE, lifespan=lifespan, dependencies=[Depends(auth)])


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


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
        "home_dir": str(config.HOME_DIR),
        "first_run": not config.AI_ENABLED and not notify.channels(),
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


# --------------------------------------------------------------------------- 設定
@app.get("/api/settings")
def get_settings() -> list[dict[str, Any]]:
    return config.get_settings()


@app.put("/api/settings")
def put_settings(values: dict[str, Any]) -> dict[str, Any]:
    try:
        config.save_settings(values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ai.reset_client()
    return {"ok": True}


@app.post("/api/settings/test-ai")
async def test_ai() -> dict[str, Any]:
    if not config.AI_ENABLED:
        return {"ok": False, "message": "尚未填寫 API Key"}
    try:
        reply = await ai.test_connection()
        return {"ok": True, "message": f"連線成功，模型回覆：{reply[:200]}"}
    except Exception as e:
        return {"ok": False, "message": f"連線失敗：{e}"}


@app.post("/api/settings/test-notify")
async def test_notify() -> dict[str, Any]:
    res = await notify.send_test()
    if not res:
        return {"ok": False, "message": "尚未設定任何通知管道"}
    lines = [f"{ch}：{'✅ 成功' if err is None else '❌ ' + err}" for ch, err in res.items()]
    return {"ok": all(e is None for e in res.values()), "message": "\n".join(lines)}


@app.get("/api/prompt")
def get_prompt() -> dict[str, str]:
    return {"text": (config.PROMPTS_DIR / "base_prompt.txt").read_text(encoding="utf-8")}


@app.put("/api/prompt")
def put_prompt(body: dict[str, str]) -> dict[str, Any]:
    text = body.get("text", "")
    if "{{CRITERIA}}" not in text:
        raise HTTPException(400, "提示詞必須保留 {{CRITERIA}}（任務需求會填在這裡）")
    (config.PROMPTS_DIR / "base_prompt.txt").write_text(text, encoding="utf-8")
    return {"ok": True}
