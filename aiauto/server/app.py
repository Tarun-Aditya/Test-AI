"""Web app + agent hub (FastAPI + SQLite).

- React UI (built into static/app) served at /.
- REST API under /api: runs (dashboard, details, progress), parse, script library, sessions, machines.
- Agents connect to /ws/agent and execute jobs on their own machine; artifacts are uploaded back.
- Browsers subscribe to /ws/ui for live run events (step progress, screenshots, live frames).
- All history lives in a local SQLite DB (AIAUTO_DB, default ./aiauto.db): restarting the hub keeps
  every past run, the library, saved sessions and the list of registered machines.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..data import MASK, iterations, load_data, lookup, masked_data, normalize, setpath
from ..db import now_iso, open_db
from ..models import RunConfig, Step, TestCase
from ..nlp import llm
from ..nlp.parser import parse_text
from ..runner import new_run_id, run_test
from ..scriptgen import extract_spec

log = logging.getLogger("aiauto.server")

ARTIFACTS = Path(os.getenv("AIAUTO_ARTIFACTS", "artifacts")).resolve()
ARTIFACTS.mkdir(parents=True, exist_ok=True)
AGENT_TOKEN = os.getenv("AIAUTO_AGENT_TOKEN")          # shared secret agents must present (optional)
LOCAL_WORKERS = int(os.getenv("AIAUTO_LOCAL_WORKERS", "2"))
LOCAL_NAME = os.getenv("AIAUTO_LOCAL_NAME", "local")
STATIC = Path(__file__).parent / "static"
APP_DIR = STATIC / "app"                               # React build output (web/ -> npm run build)
TRANSIENT = {"live_frame", "step_shot"}                # streamed to browsers, never stored

db = open_db()
app = FastAPI(title="Autopilot - AI Test Automation", version="0.3.0")
if (APP_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=APP_DIR / "assets"), name="assets")
pool = ThreadPoolExecutor(max_workers=LOCAL_WORKERS)
# All database writes for run events go through ONE thread, in the order the events were emitted
# (a run's step updates and its final summary sync must never apply out of order).
db_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")


# --------------------------------------------------------------------- live state
class Hub:
    def __init__(self):
        self.ui: set[WebSocket] = set()
        self.agents: dict[str, dict[str, Any]] = {}      # connected agents: name -> {ws, info, busy, queue}
        self.events: dict[str, list[dict]] = {}          # run_id -> events of in-flight runs (for late viewers)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def broadcast(self, event: dict) -> None:
        rid, t = event.get("run_id"), event.get("type")
        if t == "_finalize":       # internal: sync the DB with summary.json after all of the run's events
            try:
                await asyncio.get_running_loop().run_in_executor(db_writer, db.finalize_from_summary, ARTIFACTS / rid)
            except Exception as e:
                log.warning("finalize failed for %s: %s", rid, e)
            event = {"type": "runs_changed"}
            rid, t = None, "runs_changed"
        if rid and t not in TRANSIENT:
            try:
                await asyncio.get_running_loop().run_in_executor(db_writer, db.on_event, event)
            except Exception as e:
                log.warning("db update failed for %s: %s", t, e)
            self.events.setdefault(rid, []).append(event)
            if t == "run_finished":
                self.events.pop(rid, None)
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
    interrupted = db.recover()
    db.backfill_parser(ARTIFACTS)
    imported = db.import_artifacts(ARTIFACTS)
    _import_legacy_files()
    log.info("db=%s artifacts=%s | recovered %d interrupted, imported %d runs | LLM: %s",
             db.path, ARTIFACTS, interrupted, imported, llm.status())


def _import_legacy_files() -> None:
    """One-time import of file-based library scripts / sessions from earlier versions."""
    for d, kind in ((Path(os.getenv("AIAUTO_LIBRARY", "library")), "lib"), (Path(os.getenv("AIAUTO_SESSIONS", "sessions")), "ses")):
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            try:
                if kind == "lib" and not db.lib_meta(p.stem):
                    db.lib_put(p.stem, json.loads(p.read_text()))
                elif kind == "ses" and db.session_get(p.stem) is None:
                    db.session_put(p.stem, json.loads(p.read_text()))
            except Exception:
                pass


# --------------------------------------------------------------------- models
class ParseRequest(BaseModel):
    text: str
    data: Optional[Any] = None
    sensitive: list[str] = Field(default_factory=list)
    use_llm: bool = True


class Iteration(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)   # values for this iteration (merged over shared data)
    browser: Optional[str] = None                        # per-iteration browser override
    engine: Optional[str] = None                         # per-iteration engine override
    label: Optional[str] = None


class RunRequest(BaseModel):
    text: Optional[str] = None                 # plain English (AI mode)
    steps: Optional[list[Step]] = None         # already-parsed / edited steps
    replay_run_id: Optional[str] = None        # re-run the script of an earlier run
    library: Optional[str] = None              # re-run a script from the library
    name: Optional[str] = None
    data: Optional[Any] = None                 # dict, JSON string, or "k=v;k2=v2"
    sensitive: list[str] = Field(default_factory=list)
    iterations: Optional[list[Iteration]] = None
    config: RunConfig = Field(default_factory=RunConfig)
    agent: str = "local"                       # "local", "any", or a machine name
    reuse_secrets: bool = True                 # replay: fill sensitive values from the original run (kept on the hub)


class SaveScript(BaseModel):
    run_id: str
    name: Optional[str] = None


# --------------------------------------------------------------------- UI
@app.get("/", response_class=HTMLResponse)
def index():
    page = APP_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>UI not built</h1><p>Run <code>cd web && npm install && npm run build</code>.</p>", 500)
    return page.read_text(encoding="utf-8")


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    hub.ui.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.ui.discard(ws)


# --------------------------------------------------------------------- API: parse & run
@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm.status(), "llm_on": llm.available(), "agents": len(hub.agents), "local_name": LOCAL_NAME,
            "db": db.path}


@app.post("/api/parse")
def api_parse(req: ParseRequest):
    data = _data(req.data)
    test, info = parse_text(req.text, data, use_llm=req.use_llm, sensitive=req.sensitive)
    return {"test": json.loads(test.model_dump_json()), **info}


@app.post("/api/runs")
async def api_run(req: RunRequest):
    data, sens = normalize(_data(req.data), req.sensitive)
    mode = "ai"
    parser = "user"
    source_run = None
    if req.replay_run_id or req.library:
        if req.replay_run_id:
            src = ARTIFACTS / _safe(req.replay_run_id) / "script.json"
            if not src.exists():
                raise HTTPException(404, "script not found for that run")
            test = TestCase.model_validate_json(src.read_text())
            source_run = req.replay_run_id
            # same data as the original run, secrets from the hub's vault, then any edits from the request
            base = test.data
            if req.reuse_secrets:
                for k, v in db.get_secrets(req.replay_run_id).items():
                    base = setpath(base, k, v)
            data = _merge(base, data)
        else:
            raw = db.lib_get(req.library)
            if raw is None:
                raise HTTPException(404, "library script not found")
            test = TestCase.model_validate(raw)
        test = test.model_copy(update={"data": data if source_run else {**test.data, **data},
                                       "sensitive": sorted(set(test.sensitive) | set(sens))})
        mode, parser = "replay", "script"
    elif req.steps:
        test = TestCase(name=req.name or "Edited test", steps=req.steps, data=data, sensitive=sens, source_text=req.text or "")
        srcs = {s.source for s in req.steps}
        parser = "ai" if "ai" in srcs else "rules" if "rules" in srcs else "user"
    elif req.text:
        test, info = await asyncio.to_thread(parse_text, req.text, data, req.name, req.config.use_llm, sens)
        if info["unparsed"]:
            raise HTTPException(422, {"message": "Some sentences were not understood", "unparsed": info["unparsed"]})
        parser = "ai" if info["parser"].startswith("llm") else "rules"
    else:
        raise HTTPException(400, "Provide text, steps, replay_run_id or library")

    cfg = req.config
    if cfg.session:
        cfg.session_state = db.session_get(cfg.session)
        if cfg.session_state is None:
            raise HTTPException(404, f"No saved session '{cfg.session}'")

    plan: list[tuple[dict, RunConfig, str | None]] = []
    if req.iterations:
        for i, it in enumerate(req.iterations, 1):
            row_data, row_sens = normalize(it.data, [])
            c = cfg.model_copy(update={k: v for k, v in (("browser", it.browser), ("engine", it.engine)) if v})
            c.session_state = cfg.session_state
            plan.append(({**test.data, **row_data}, c, it.label or f"#{i}"))
            test.sensitive = sorted(set(test.sensitive) | set(row_sens))
    else:
        rows = iterations(test.data)
        plan = [(row, cfg, f"#{i}" if len(rows) > 1 else None) for i, row in enumerate(rows, 1)]

    if req.agent not in ("local", "any") and req.agent not in hub.agents:
        raise HTTPException(404, f"Machine '{req.agent}' is not connected")

    batch = uuid.uuid4().hex[:8] if len(plan) > 1 else None
    labels = _iteration_labels([(row, c) for row, c, _ in plan], test.sensitive) if len(plan) > 1 else [None]
    run_ids = []
    for (row, c, _), label in zip(plan, labels):
        name = _display_name(test.name, row, test.sensitive)
        t = test.model_copy(update={"data": row, "name": f"{name} · {label}" if label else name})
        rid = new_run_id(t.name)
        run_ids.append(rid)
        job = {"run_id": rid, "test": json.loads(t.model_dump_json()), "config": c.model_dump(),
               "session_state": c.session_state, "mode": mode, "agent": req.agent}
        db.create_run({"run_id": rid, "test_name": t.name, "status": "queued", "engine": c.engine, "browser": c.browser,
                       "agent": req.agent, "mode": mode, "started_at": now_iso(), "total": len(t.steps),
                       "iteration": label, "batch_id": batch, "config_json": c.model_dump_json(),
                       "data_json": json.dumps(masked_data(row, t.sensitive)), "sensitive_json": json.dumps(t.sensitive),
                       "source_run": source_run, "parser": parser})
        secrets = {}
        for k in t.sensitive:
            try:
                v = lookup(row, k)
                if v not in (None, "", MASK):
                    secrets[k] = v
            except (KeyError, IndexError, ValueError):
                pass
        db.put_secrets(rid, secrets)
        await _dispatch(job)
    await hub.broadcast({"type": "runs_changed"})
    return {"run_ids": run_ids, "batch_id": batch}


@app.get("/api/runs")
def api_runs(limit: int = 200, status: Optional[str] = None, q: Optional[str] = None):
    return db.list_runs(limit, status, q)


@app.get("/api/stats")
def api_stats():
    return db.stats()


@app.get("/api/runs/{run_id}")
def api_run_get(run_id: str):
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    if r["live"]:
        r["events"] = hub.events.get(run_id, [])
    return r


@app.get("/api/runs/{run_id}/files")
def api_files(run_id: str):
    """Flat list of a run's generated files (the UI renders it as a tree)."""
    d = ARTIFACTS / _safe(run_id)
    if not d.exists():
        raise HTTPException(404)
    return [{"path": str(f.relative_to(d)).replace(os.sep, "/"), "size": f.stat().st_size}
            for f in sorted(d.rglob("*")) if f.is_file()]


@app.get("/api/runs/{run_id}/export")
def api_export(run_id: str):
    """Self-runnable Python project: scripts for both engines, config, every iteration's data, run.py."""
    from ..export import build_project
    r = db.raw_run(_safe(run_id))
    if not r:
        raise HTTPException(404)
    members = db.batch_runs(r["batch_id"]) if r.get("batch_id") else [r]
    try:
        name, blob = build_project(ARTIFACTS, r, members)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    return StreamingResponse(io.BytesIO(blob), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


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
    db.delete_run(run_id)
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


# --------------------------------------------------------------------- API: script library
@app.get("/api/library")
def library_list():
    return db.lib_list()


@app.post("/api/library/from-run")
def library_from_run(req: SaveScript):
    src = ARTIFACTS / _safe(req.run_id) / "script.json"
    if not src.exists():
        raise HTTPException(404)
    t = json.loads(src.read_text())
    return db.lib_put(_slug(req.name or t["name"]), t, source_run=req.run_id)


@app.post("/api/library/upload")
async def library_upload(file: UploadFile = File(...), name: Optional[str] = Form(None)):
    """Upload a script.json or a generated test_playwright.py / test_selenium.py (the spec is embedded)."""
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        t = extract_spec(raw)
    except Exception as e:
        raise HTTPException(400, f"Unrecognised script: {e}")
    return db.lib_put(_slug(name or t.name), json.loads(t.model_dump_json()))


@app.get("/api/library/{name}")
def library_get(name: str):
    t = db.lib_get(name)
    if t is None:
        raise HTTPException(404)
    return t


@app.delete("/api/library/{name}")
def library_delete(name: str):
    db.lib_delete(name)
    return {"deleted": name}


# --------------------------------------------------------------------- API: sessions
@app.get("/api/sessions")
def sessions_list():
    return db.session_list()


@app.post("/api/sessions/{name}")
async def sessions_upload(name: str, file: UploadFile = File(...)):
    """Import a Playwright storage_state.json (cookies + localStorage) as a named session."""
    try:
        state = json.loads(await file.read())
        assert isinstance(state.get("cookies", []), list)
        db.session_put(_slug(name), state)
    except Exception as e:
        raise HTTPException(400, f"Invalid storage state: {e}")
    return {"saved": name}


@app.delete("/api/sessions/{name}")
def sessions_delete(name: str):
    db.session_delete(name)
    return {"deleted": name}


# --------------------------------------------------------------------- API: machines
@app.get("/api/agents")
def api_agents():
    """Every machine that ever registered, with live state for the connected ones."""
    out = []
    for m in db.machines():
        live = hub.agents.get(m["name"])
        m["online"] = bool(live)
        m["busy"] = bool(live and live["busy"])
        m["queued"] = live["queue"].qsize() if live else 0
        out.append(m)
    return out


@app.delete("/api/agents/{name}")
def api_agent_forget(name: str):
    if name in hub.agents:
        raise HTTPException(409, "Machine is connected; stop its agent first")
    db.machine_delete(name)
    return {"deleted": name}


# --------------------------------------------------------------------- dispatch
async def _dispatch(job: dict) -> None:
    target = job["agent"]
    if target == "local" or (target == "any" and not hub.agents):
        job["agent"] = LOCAL_NAME
        db.update_run(job["run_id"], agent=LOCAL_NAME)
        pool.submit(_run_local, job)
        return
    if target == "any":
        target = min(hub.agents, key=lambda n: (hub.agents[n]["busy"], hub.agents[n]["queue"].qsize()))
        job["agent"] = target
        db.update_run(job["run_id"], agent=target)
    db.update_run(job["run_id"], status="dispatched")
    await hub.agents[target]["queue"].put(job)


def _run_local(job: dict) -> None:
    try:
        test = TestCase.model_validate(job["test"])
        cfg = RunConfig.model_validate(job["config"])
        cfg.session_state = job.get("session_state")
        holder: list = []
        run_test(test, cfg, ARTIFACTS, mode=job["mode"], on_event=hub.emit_threadsafe, agent=LOCAL_NAME,
                 run_id=job["run_id"], runner_out=holder)
        hub.emit_threadsafe({"type": "_finalize", "run_id": job["run_id"]})   # ordered after the run's events
        if cfg.save_session and holder and holder[0].saved_session:
            db.session_put(cfg.save_session, holder[0].saved_session)
            hub.emit_threadsafe({"type": "session_saved", "name": cfg.save_session})
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
    name = _slug(hello.get("name") or f"agent-{len(hub.agents) + 1}")
    if name in hub.agents or name == LOCAL_NAME:
        name = f"{name}-{os.urandom(2).hex()}"
    queue: asyncio.Queue = asyncio.Queue()
    info = {k: hello.get(k) for k in ("hostname", "platform", "engines", "browsers", "version")}
    hub.agents[name] = {"ws": ws, "busy": False, "queue": queue, "info": info}
    db.machine_seen(name, info, online=True)
    await ws.send_json({"type": "welcome", "name": name})
    await hub.broadcast({"type": "agents_changed"})
    log.info("agent connected: %s", name)
    current: dict = {}

    async def sender():
        while True:
            job = await queue.get()
            hub.agents[name]["busy"] = True
            current["run_id"] = job["run_id"]
            await ws.send_json({"type": "job", "job": {k: job.get(k) for k in ("run_id", "test", "config", "mode", "session_state")}})

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
                    current.pop("run_id", None)
            elif msg.get("type") == "session":
                try:
                    db.session_put(_slug(msg["name"]), msg["state"])
                    await hub.broadcast({"type": "session_saved", "name": msg["name"]})
                except Exception as e:
                    log.warning("could not save session from %s: %s", name, e)
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        hub.agents.pop(name, None)
        db.machine_offline(name)
        lost = [current["run_id"]] if current.get("run_id") else []
        while not queue.empty():
            lost.append(queue.get_nowait()["run_id"])
        for rid in lost:
            await hub.broadcast({"type": "run_finished", "run_id": rid, "status": "interrupted",
                                 "error": f"Machine '{name}' disconnected"})
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
    await hub.broadcast({"type": "_finalize", "run_id": run_id})
    await hub.broadcast({"type": "artifacts_uploaded", "run_id": run_id})
    return {"ok": True}


# --------------------------------------------------------------------- SPA fallback
@app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "artifacts/", "ws/")):
        raise HTTPException(404)
    f = (APP_DIR / path).resolve()
    if f.is_file() and str(f).startswith(str(APP_DIR.resolve())):
        return FileResponse(f)
    return index()


# --------------------------------------------------------------------- helpers
def _data(d: Any) -> dict:
    if d is None or d == "":
        return {}
    try:
        return load_data(d)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Invalid data: {e}")


def _display_name(name: str, row: dict, sensitive: list[str]) -> str:
    """Fill ${placeholders} in a test name with this run's values (secrets stay masked)."""
    from ..data import Masker, substitute
    return Masker(row, sensitive)(substitute(name, row)) or name


def _iteration_labels(rows: list[tuple[dict, RunConfig]], sensitive: list[str]) -> list[str]:
    """'#2 HP, qty 1 · chrome': the iteration number plus the data values and browser that differ."""
    from ..data import flatten_items
    flat = [dict(flatten_items(r)) for r, _ in rows]
    keys = [k for k in dict.fromkeys(k for f in flat for k in f)
            if k not in sensitive and len({str(f.get(k)) for f in flat}) > 1][:2]
    browsers_differ = len({c.browser for _, c in rows}) > 1
    out = []
    for i, (f, (_, c)) in enumerate(zip(flat, rows), 1):
        bits = [str(f.get(k))[:24] if j == 0 else f"{k.split('.')[-1]} {str(f.get(k))[:16]}" for j, k in enumerate(keys)]
        label = f"#{i}" + (f" {', '.join(bits)}" if bits else "")
        out.append(label + (f" · {c.browser}" if browsers_differ else ""))
    return out


def _merge(a: dict, b: dict) -> dict:
    """Deep-merge b over a; masked placeholders in b never overwrite real values."""
    out = dict(a or {})
    for k, v in (b or {}).items():
        if v == MASK and k in out:
            continue
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _safe(name: str) -> str:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "bad name")
    return name


def _slug(s: str) -> str:
    return re.sub(r"[^\w.-]+", "-", (s or "").strip()).strip("-")[:60] or "script"
