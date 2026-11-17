"""Web app + agent hub.

- Web UI at /            (write English, add data, run, watch live, browse artifacts, replay)
- REST API under /api    (see README)
- Agents connect to /ws/agent and execute jobs on their own machine; artifacts are uploaded back.
- Browsers / dashboards subscribe to /ws/ui for live run events.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..data import iterations, load_data
from ..models import RunConfig, RunSummary, Step, TestCase
from ..nlp import llm
from ..nlp.parser import parse_text
from ..runner import load_script, new_run_id, run_test

log = logging.getLogger("aiauto.server")

ARTIFACTS = Path(os.getenv("AIAUTO_ARTIFACTS", "artifacts")).resolve()
ARTIFACTS.mkdir(parents=True, exist_ok=True)
AGENT_TOKEN = os.getenv("AIAUTO_AGENT_TOKEN")          # shared secret agents must present (optional)
LOCAL_WORKERS = int(os.getenv("AIAUTO_LOCAL_WORKERS", "2"))
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="AI Test Automation", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
pool = ThreadPoolExecutor(max_workers=LOCAL_WORKERS)


# --------------------------------------------------------------------- state
class Hub:
    def __init__(self):
        self.ui: set[WebSocket] = set()
        self.agents: dict[str, dict[str, Any]] = {}      # name -> {ws, info, busy, queue}
        self.jobs: dict[str, dict[str, Any]] = {}        # run_id -> job record
        self.loop: asyncio.AbstractEventLoop | None = None

    async def broadcast(self, event: dict) -> None:
        rid = event.get("run_id")
        if rid in self.jobs:
            job = self.jobs[rid]
            job.setdefault("events", []).append(event)
            if event["type"] == "step_finished":
                job["done"] = job.get("done", 0) + 1
            if event["type"] == "run_finished":
                job["status"] = event.get("status", "finished")
        dead = []
        for ws in list(self.ui):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ui.discard(ws)

    def emit_threadsafe(self, event: dict) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.broadcast(event), self.loop)


hub = Hub()


@app.on_event("startup")
async def _startup():
    hub.loop = asyncio.get_running_loop()
    log.info("artifacts: %s | LLM: %s", ARTIFACTS, llm.status())


# --------------------------------------------------------------------- models
class ParseRequest(BaseModel):
    text: str
    data: Optional[Any] = None
    use_llm: bool = True


class RunRequest(BaseModel):
    text: Optional[str] = None                 # plain English (AI mode)
    steps: Optional[list[Step]] = None         # already-parsed / edited steps
    replay_run_id: Optional[str] = None        # re-run the script.json of an earlier run
    name: Optional[str] = None
    data: Optional[Any] = None                 # dict, JSON string, or "k=v;k2=v2"
    config: RunConfig = Field(default_factory=RunConfig)
    agent: str = "local"                       # "local", "any", or an agent name


# --------------------------------------------------------------------- UI
@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    hub.ui.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.ui.discard(ws)


# --------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm.status(), "agents": len(hub.agents)}


@app.post("/api/parse")
def api_parse(req: ParseRequest):
    data = _data(req.data)
    test, info = parse_text(req.text, data, use_llm=req.use_llm)
    return {"test": json.loads(test.model_dump_json()), **info}


@app.post("/api/runs")
async def api_run(req: RunRequest):
    data = _data(req.data)
    mode = "ai"
    if req.replay_run_id:
        script = ARTIFACTS / _safe(req.replay_run_id) / "script.json"
        if not script.exists():
            raise HTTPException(404, "script.json not found for that run")
        test = load_script(script)
        data = {**test.data, **data}
        mode = "replay"
    elif req.steps:
        test = TestCase(name=req.name or "Edited test", steps=req.steps, data=data, source_text=req.text or "")
    elif req.text:
        test, info = await asyncio.to_thread(parse_text, req.text, data, req.name, req.config.use_llm)
        if info["unparsed"]:
            raise HTTPException(422, {"message": "Some sentences were not understood", "unparsed": info["unparsed"]})
    else:
        raise HTTPException(400, "Provide text, steps or replay_run_id")

    run_ids = []
    for row in iterations(data):
        t = test.model_copy(update={"data": row})
        rid = new_run_id(t.name)
        run_ids.append(rid)
        job = {"run_id": rid, "test": json.loads(t.model_dump_json()), "config": req.config.model_dump(),
               "mode": mode, "status": "queued", "agent": req.agent, "name": t.name, "total": len(t.steps), "done": 0}
        hub.jobs[rid] = job
        await _dispatch(job)
    return {"run_ids": run_ids}


@app.get("/api/runs")
def api_runs(limit: int = 100):
    out = []
    for rid, job in hub.jobs.items():
        if job["status"] in ("queued", "running", "dispatched"):
            out.append({"run_id": rid, "test_name": job["name"], "status": job["status"], "agent": job["agent"],
                        "engine": job["config"]["engine"], "mode": job["mode"], "started_at": "", "progress":
                        f'{job.get("done", 0)}/{job["total"]}'})
    for p in sorted(ARTIFACTS.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            s = json.loads(p.read_text())
        except Exception:
            continue
        out.append({k: s.get(k) for k in ("run_id", "test_name", "status", "agent", "engine", "mode", "started_at",
                                          "duration_ms", "video", "artifacts")})
    return out


@app.get("/api/runs/{run_id}")
def api_run_get(run_id: str):
    p = ARTIFACTS / _safe(run_id) / "summary.json"
    if p.exists():
        return json.loads(p.read_text())
    if run_id in hub.jobs:
        j = hub.jobs[run_id]
        return {"run_id": run_id, "status": j["status"], "events": j.get("events", [])}
    raise HTTPException(404)


@app.get("/api/runs/{run_id}/script")
def api_script(run_id: str):
    p = ARTIFACTS / _safe(run_id) / "script.json"
    if not p.exists():
        raise HTTPException(404)
    return json.loads(p.read_text())


@app.delete("/api/runs/{run_id}")
def api_delete(run_id: str):
    d = ARTIFACTS / _safe(run_id)
    if d.exists():
        shutil.rmtree(d)
    hub.jobs.pop(run_id, None)
    return {"deleted": run_id}


@app.get("/api/runs/{run_id}/download")
def api_download(run_id: str):
    d = ARTIFACTS / _safe(run_id)
    if not d.exists():
        raise HTTPException(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})


@app.get("/artifacts/{run_id}/{path:path}")
def artifact(run_id: str, path: str):
    base = (ARTIFACTS / _safe(run_id)).resolve()
    f = (base / path).resolve()
    if not str(f).startswith(str(base)) or not f.is_file():
        raise HTTPException(404)
    media = "text/plain; charset=utf-8" if f.suffix in (".py", ".txt") else None
    return FileResponse(f, media_type=media)


@app.get("/api/agents")
def api_agents():
    return [{"name": n, **a["info"], "busy": a["busy"], "queued": a["queue"].qsize()} for n, a in hub.agents.items()]


# --------------------------------------------------------------------- dispatch
async def _dispatch(job: dict) -> None:
    target = job["agent"]
    if target == "local" or (target == "any" and not hub.agents):
        job["agent"] = "local"
        job["status"] = "running"
        pool.submit(_run_local, job)
        return
    if target == "any":
        target = min(hub.agents, key=lambda n: (hub.agents[n]["busy"], hub.agents[n]["queue"].qsize()))
        job["agent"] = target
    agent = hub.agents.get(target)
    if not agent:
        job["status"] = "failed"
        raise HTTPException(404, f"Agent '{target}' is not connected")
    job["status"] = "dispatched"
    await agent["queue"].put(job)


def _run_local(job: dict) -> None:
    try:
        test = TestCase.model_validate(job["test"])
        cfg = RunConfig.model_validate(job["config"])
        run_test(test, cfg, ARTIFACTS, mode=job["mode"], on_event=hub.emit_threadsafe, agent="local", run_id=job["run_id"])
    except Exception as e:
        log.exception("local run failed")
        hub.emit_threadsafe({"type": "run_finished", "run_id": job["run_id"], "status": "failed", "error": str(e)})


# --------------------------------------------------------------------- agents
@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    hello = await ws.receive_json()
    if AGENT_TOKEN and hello.get("token") != AGENT_TOKEN:
        await ws.send_json({"type": "error", "message": "invalid token"})
        await ws.close(code=4401)
        return
    name = hello.get("name") or f"agent-{len(hub.agents) + 1}"
    if name in hub.agents:
        name = f"{name}-{os.urandom(2).hex()}"
    queue: asyncio.Queue = asyncio.Queue()
    hub.agents[name] = {"ws": ws, "busy": False, "queue": queue,
                        "info": {k: hello.get(k) for k in ("hostname", "platform", "engines", "version")}}
    await ws.send_json({"type": "welcome", "name": name})
    await hub.broadcast({"type": "agents_changed"})
    log.info("agent connected: %s", name)

    async def sender():
        while True:
            job = await queue.get()
            hub.agents[name]["busy"] = True
            job["status"] = "running"
            await ws.send_json({"type": "job", "job": {k: job[k] for k in ("run_id", "test", "config", "mode")}})

    send_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "event":
                ev = msg["event"]
                ev["agent"] = name
                await hub.broadcast(ev)
                if ev.get("type") == "run_finished":
                    hub.agents[name]["busy"] = False
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        # requeue anything not yet started
        hub.agents.pop(name, None)
        while not queue.empty():
            j = queue.get_nowait()
            j["status"] = "failed"
        await hub.broadcast({"type": "agents_changed"})
        log.info("agent disconnected: %s", name)


@app.post("/api/agents/upload/{run_id}")
async def agent_upload(run_id: str, file: UploadFile = File(...), x_agent_token: Optional[str] = Header(None)):
    if AGENT_TOKEN and x_agent_token != AGENT_TOKEN:
        raise HTTPException(401)
    dest = ARTIFACTS / _safe(run_id)
    dest.mkdir(parents=True, exist_ok=True)
    raw = await file.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for m in z.infolist():
            target = (dest / m.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue  # zip-slip guard
            if m.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(m))
    if run_id in hub.jobs and hub.jobs[run_id]["status"] in ("running", "dispatched"):
        hub.jobs[run_id]["status"] = "finished"
    await hub.broadcast({"type": "artifacts_uploaded", "run_id": run_id})
    return {"ok": True}


# --------------------------------------------------------------------- helpers
def _data(d: Any) -> dict:
    if d is None or d == "":
        return {}
    try:
        return load_data(d)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Invalid data: {e}")


def _safe(run_id: str) -> str:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise HTTPException(400, "bad run id")
    return run_id
