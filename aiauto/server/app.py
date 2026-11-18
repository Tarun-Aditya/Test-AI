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
import secrets
import shutil
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal, Optional, Union

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse as _JSON
from pydantic import BaseModel, Field

from ..data import MASK, iterations, load_data, lookup, masked_data, normalize, setpath
from ..db import now_iso, open_db
from ..models import RunConfig, Step, TestCase
from ..nlp import llm, providers
from . import auth as authmod
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
grid_pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="grid")   # runs whose browser is on a remote grid
# All database writes for run events go through ONE thread, in the order the events were emitted
# (a run's step updates and its final summary sync must never apply out of order).
db_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")
AUTH = authmod.Auth(db, Path(db.path).resolve().parent)
db.vault = AUTH.vault


def _agent_token(token: str | None) -> dict | None:
    """A machine's credential: the shared AIAUTO_AGENT_TOKEN or a per-machine token (Machines → Agent tokens).
    Returns {"id": ...} (None id for the shared one), or None when refused. Without accounts and without any
    token configured, agents may connect freely (single-user hub on a trusted network)."""
    import hashlib
    if token and AGENT_TOKEN and secrets.compare_digest(token, AGENT_TOKEN):
        return {"id": None}
    if token:
        row = db.agent_token_find(hashlib.sha256(token.encode()).hexdigest())
        if row:
            return row
    if not AGENT_TOKEN and not authmod.enabled() and db.agent_token_count() == 0:
        return {"id": None}
    return None


def _org_ai() -> dict:
    """The organisation (hub) AI settings, with its keys decrypted."""
    cfg = dict(db.setting_get("ai", {}) or {})
    for k in ("anthropic_key", "litellm_key"):
        v = cfg.get(k)
        if isinstance(v, str) and v.startswith("enc:"):
            cfg[k] = AUTH.vault.dec(v[4:])
    return cfg


def _org_ai_save(cfg: dict) -> None:
    out = dict(cfg)
    for k in ("anthropic_key", "litellm_key"):
        if out.get(k) and not str(out[k]).startswith("enc:"):
            out[k] = "enc:" + AUTH.vault.enc(out[k])
    db.setting_put("ai", out)


def _user(request) -> dict:
    return getattr(request.state, "user", None) or AUTH.user_from_request(request) or authmod.LOCAL_USER


def _owner_of(request) -> str | None:
    return _user(request)["username"] if authmod.enabled() else None


class _AllProjects:
    def __contains__(self, key):
        return True


ALL_PROJECTS = _AllProjects()


def _projects_for(user: dict | None):
    """Projects this person may see. No accounts or admin: all of them."""
    if not authmod.enabled() or not user or user.get("role") == "admin" or not user.get("id"):
        return ALL_PROJECTS
    return db.user_projects(user["id"])


def _project(request) -> str:
    """The project a request works in (the project switcher sends it as X-Project)."""
    key = request.headers.get("x-project") or request.query_params.get("project") or "default"
    if key != "default" and not db.project(key):
        raise HTTPException(404, f"No project '{key}'")
    if key not in _projects_for(_user(request)):
        raise HTTPException(403, f"You are not a member of project '{key}'")
    return key


def _ai_scope(username: str | None) -> dict | None:
    """AI settings for work done for this user: their own subscription / key, or the organisation's."""
    if not authmod.enabled() or not username:
        return None
    u = db.user_get(username=username)
    return AUTH.ai_for(u, _org_ai()) if u else None


def _as_user(username: str | None, fn, *a, **k):
    """Run fn with this user's AI settings in effect (call it inside the worker thread)."""
    with providers.using(_ai_scope(username)):
        return fn(*a, **k)


@app.middleware("http")
async def _auth_gate(request, call_next):
    """Sign-in required for the API and artifacts; roles decide what you may change; CSRF header on writes."""
    path = request.url.path
    if not authmod.enabled() or authmod.is_public(path):
        return await call_next(request)
    user = AUTH.user_from_request(request)
    if not user:
        return _JSON({"detail": "Sign in first"}, status_code=401)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(authmod.CSRF_HEADER) != "autopilot":
        return _JSON({"detail": "Missing X-Requested-With header"}, status_code=403)
    why = authmod.denied(user, request.method, path)
    if why:
        return _JSON({"detail": why}, status_code=403)
    m = re.match(r"^/(?:api/runs|artifacts)/([^/]+)", path)
    if m and m.group(1) not in ("cancel",):
        p = db.run_project(m.group(1))
        if p and p not in _projects_for(user):
            return _JSON({"detail": "Not found"}, status_code=404)      # another project's run: invisible
    request.state.user = user
    return await call_next(request)


app.include_router(authmod.make_router(AUTH, _org_ai, providers))


# --------------------------------------------------------------------- live state
class Hub:
    def __init__(self):
        self.ui: set[WebSocket] = set()
        self.ui_user: dict[WebSocket, dict] = {}          # who is watching (live events are filtered by project)
        self.run_project: dict[str, str] = {}             # run id -> project (cache for event filtering)
        self.agents: dict[str, dict[str, Any]] = {}      # connected agents: name -> {ws, info, slots, running, send_lock}
        self.pending: list[dict] = []                    # jobs waiting for a free slot on one of their machines
        self.local_running: set[str] = set()             # run ids executing on the hub itself
        self.local_runners: dict[str, list] = {}         # run id -> [Runner] (to cancel a local run)
        self.grid_running: dict[str, set[str]] = {}      # grid name -> run ids using it
        self.events: dict[str, list[dict]] = {}          # run_id -> events of in-flight runs (for late viewers)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def broadcast(self, event: dict) -> None:
        rid, t = event.get("run_id"), event.get("type")
        if t == "_finalize":       # internal: sync the DB with summary.json after all of the run's events
            try:
                await asyncio.get_running_loop().run_in_executor(db_writer, db.finalize_from_summary, ARTIFACTS / rid)
                integrations_api.on_run_finished(db, rid, ARTIFACTS)      # tracker / test-tool write-back
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
        project = None
        if rid:
            project = self.run_project.get(rid) or db.run_project(rid) or "default"
            self.run_project[rid] = project
        for ws in list(self.ui):
            if project and project not in _projects_for(self.ui_user.get(ws)):
                continue                                  # another project's steps / screenshots stay there
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ui.discard(ws)
            self.ui_user.pop(ws, None)

    def emit_threadsafe(self, event: dict) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.broadcast(event), self.loop)


hub = Hub()


@app.on_event("startup")
async def _startup():
    hub.loop = asyncio.get_running_loop()
    if any(str(v).startswith(("sk-", "sk_")) for v in (db.setting_get("ai", {}) or {}).values()):
        _org_ai_save(_org_ai())                 # encrypt keys saved by an older version
    providers.configure(_org_ai())
    AUTH.bootstrap()
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
                    db.lib_put(p.stem, json.loads(p.read_text(encoding="utf-8")))
                elif kind == "ses" and db.session_get(p.stem) is None:
                    db.session_put(p.stem, json.loads(p.read_text(encoding="utf-8")))
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
    agent: Union[str, list[str]] = "local"     # "local", "any", a machine name, or several machine names
    spread: Literal["each", "pool"] = "each"   # several machines: run everything on EACH, or share the runs as a POOL
    engines: Optional[list[Literal["playwright", "selenium"]]] = None   # e.g. both: every run once per engine
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
    user = AUTH.user_from_request(ws)
    if authmod.enabled() and not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.ui.add(ws)
    hub.ui_user[ws] = user
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.ui.discard(ws)
        hub.ui_user.pop(ws, None)


# --------------------------------------------------------------------- API: parse & run
@app.get("/api/health")
def health(request: Request):
    if authmod.enabled() and not AUTH.user_from_request(request):
        return {"ok": True, "auth": True, "agents": len(hub.agents)}      # anonymous: no internals
    return {"ok": True, "llm": llm.status(), "llm_on": llm.available(), "agents": len(hub.agents), "local_name": LOCAL_NAME,
            "local_slots": LOCAL_WORKERS, "local_running": len(hub.local_running), "waiting": len(hub.pending),
            "db": db.path}


@app.post("/api/parse")
def api_parse(req: ParseRequest, request: Request):
    data = _data(req.data)
    test, info = _as_user(_owner_of(request), parse_text, req.text, data, use_llm=req.use_llm, sensitive=req.sensitive)
    return {"test": json.loads(test.model_dump_json()), **info}


@app.post("/api/runs")
async def api_run(req: RunRequest, request: Request):
    user, owner = _user(request), _owner_of(request)
    project = _project(request)
    for ref in filter(None, (req.replay_run_id,)):
        if (db.run_project(ref) or "default") not in _projects_for(user):
            raise HTTPException(404, "script not found for that run")
    if req.library and (db.lib_meta(req.library) or {}).get("project", "default") not in _projects_for(user):
        raise HTTPException(404, "library script not found")
    data, sens = normalize(_data(req.data), req.sensitive)
    mode = "ai"
    parser = "user"
    source_run = None
    if req.replay_run_id or req.library:
        if req.replay_run_id:
            src = ARTIFACTS / _safe(req.replay_run_id) / "script.json"
            if not src.exists():
                raise HTTPException(404, "script not found for that run")
            test = TestCase.model_validate_json(src.read_text(encoding="utf-8"))
            source_run = req.replay_run_id
            # same data as the original run, secrets from the hub's vault, then any edits from the request
            base = test.data
            src_owner = (db.get_run(req.replay_run_id) or {}).get("owner")
            if req.reuse_secrets and authmod.can_manage(user, src_owner):   # someone else's secrets stay theirs
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
        test, info = await asyncio.to_thread(_as_user, owner, parse_text, req.text, data, req.name, req.config.use_llm, sens)
        if info["unparsed"]:
            raise HTTPException(422, {"message": "Some sentences were not understood", "unparsed": info["unparsed"]})
        parser = "ai" if info["parser"].startswith("llm") else "rules"
    else:
        raise HTTPException(400, "Provide text, steps, replay_run_id or library")

    cfg = req.config
    if cfg.session:
        if not authmod.can_manage(user, db.session_owner(cfg.session)):
            raise HTTPException(403, f"Saved session '{cfg.session}' belongs to someone else")
        cfg.session_state = db.session_get(cfg.session)
        if cfg.session_state is None:
            raise HTTPException(404, f"No saved session '{cfg.session}'")

    plan: list[tuple[dict, RunConfig, str | None]] = []
    if req.iterations:
        for i, it in enumerate(req.iterations, 1):
            row_data, row_sens = normalize(it.data, [])
            c = cfg.model_copy(update={k: v for k, v in (("browser", it.browser), ("engine", it.engine)) if v})
            c.session_state = cfg.session_state
            plan.append((_merge(test.data, row_data), c, it.label or f"#{i}"))
            test.sensitive = sorted(set(test.sensitive) | set(row_sens))
    else:
        rows = iterations(test.data)
        plan = [(row, cfg, f"#{i}" if len(rows) > 1 else None) for i, row in enumerate(rows, 1)]

    # several engines: every planned run once per engine (Playwright and Selenium side by side)
    if req.engines and len(set(req.engines)) > 1:
        plan = [(row, c.model_copy(update={"engine": eng}), lab) for row, c, lab in plan for eng in dict.fromkeys(req.engines)]
        for _, c, _ in plan:
            c.session_state = cfg.session_state
    elif req.engines:
        for _, c, _ in plan:
            c.engine = req.engines[0]

    # machines: one, any free one, every one of a list ("each"), or a shared pool of them ("pool")
    machines = [req.agent] if isinstance(req.agent, str) else list(dict.fromkeys(req.agent))
    if not machines:
        raise HTTPException(400, "Pick at least one machine")
    grids = _grids()
    for m in machines:
        if m not in ("local", "any", LOCAL_NAME) and m not in hub.agents and m not in grids:
            raise HTTPException(404, f"Machine '{m}' is not connected")
        if m in grids:
            g = grids[m]
            bad = [c.engine for _, c, _ in plan if c.engine != g["engine"]]
            if bad:
                raise HTTPException(400, f"'{m}' is a {g['engine'].title()} grid: it cannot run {bad[0]} runs")
    machines = ["local" if m == LOCAL_NAME else m for m in machines]
    if len(machines) > 1 and req.spread == "each":
        targets = [[m] for m in machines]
        plan = [(row, c, lab, t) for row, c, lab in plan for t in targets]
    else:
        plan = [(row, c, lab, machines) for row, c, lab in plan]

    batch = uuid.uuid4().hex[:8] if len(plan) > 1 else None
    labels = _iteration_labels([(row, c, t) for row, c, _, t in plan], test.sensitive) if len(plan) > 1 else [None]
    run_ids = []
    for (row, c, _, target), label in zip(plan, labels):
        name = _display_name(test.name, row, test.sensitive)
        title = f"{name} · {label}" if label else name
        t = test.model_copy(update={"data": row, "title": title})      # name keeps its ${placeholders} for replays
        rid = new_run_id(title)
        run_ids.append(rid)
        job = {"run_id": rid, "test": json.loads(t.model_dump_json()), "config": c.model_dump(),
               "session_state": c.session_state, "mode": mode, "targets": target, "owner": owner, "project": project}
        db.create_run({"run_id": rid, "test_name": title, "status": "queued", "engine": c.engine, "browser": c.browser,
                       "agent": target[0] if len(target) == 1 and target[0] != "local" else LOCAL_NAME if target == ["local"] else "any", "mode": mode, "started_at": now_iso(), "total": len(t.steps),
                       "iteration": label, "batch_id": batch, "config_json": c.model_dump_json(),
                       "data_json": json.dumps(masked_data(row, t.sensitive)), "sensitive_json": json.dumps(t.sensitive),
                       "source_run": source_run, "parser": parser, "owner": owner, "project": project})
        hub.run_project[rid] = project
        secrets = {}
        for k in t.sensitive:
            try:
                v = lookup(row, k)
                if v not in (None, "", MASK):
                    secrets[k] = v
            except (KeyError, IndexError, ValueError):
                pass
        db.put_secrets(rid, secrets)
        hub.pending.append(job)
    await _pump()
    await hub.broadcast({"type": "runs_changed"})
    return {"run_ids": run_ids, "batch_id": batch}


class CancelRequest(BaseModel):
    run_ids: list[str] = Field(default_factory=list)
    batch_id: Optional[str] = None


async def _cancel(run_id: str) -> str:
    """queued -> removed from the queue; running -> stops after its current step (browser closed, report kept)."""
    for job in list(hub.pending):
        if job["run_id"] == run_id:
            hub.pending.remove(job)
            await hub.broadcast({"type": "run_finished", "run_id": run_id, "status": "cancelled", "error": "Cancelled before it started"})
            return "removed from queue"
    if run_id in hub.local_runners:
        h = hub.local_runners[run_id]
        if h:
            h[0].cancel.set()
        else:     # thread not started yet: it will see the flag as soon as the runner exists
            hub.local_runners[run_id] = _CancelledHolder()
        return "stopping"
    for name, a in hub.agents.items():
        if run_id in a["running"]:
            async with a["send_lock"]:
                await a["ws"].send_json({"type": "cancel", "run_id": run_id})
            return f"stopping on {name}"
    return "not running"


class _CancelledHolder(list):
    """Placeholder for a local run cancelled before its Runner existed: cancels it on append."""

    def append(self, r):
        r.cancel.set()
        super().append(r)


@app.post("/api/runs/cancel")
async def api_cancel(req: CancelRequest, request: Request):
    ids = list(req.run_ids)
    if req.batch_id:
        ids += [r["run_id"] for r in db.list_runs(1000, None, None) if r.get("batch_id") == req.batch_id]
    user = _user(request)
    out = {}
    allowed = _projects_for(user)
    for rid in dict.fromkeys(ids):
        if (db.run_project(rid) or "default") not in allowed:
            out[rid] = "not found"
            continue
        if not authmod.can_manage(user, (db.get_run(rid) or {}).get("owner")):
            out[rid] = "not yours (ask its owner or an admin)"
            continue
        out[rid] = await _cancel(rid)
    await hub.broadcast({"type": "runs_changed"})
    return {"cancelled": out}


@app.post("/api/runs/{run_id}/cancel")
async def api_cancel_one(run_id: str, request: Request):
    return await api_cancel(CancelRequest(run_ids=[run_id]), request)


@app.get("/api/runs")
def api_runs(request: Request, limit: int = 200, status: Optional[str] = None, q: Optional[str] = None, mine: bool = False):
    return db.list_runs(limit, status, q, owner=_owner_of(request) if mine else None, project=_project(request))


@app.get("/api/stats")
def api_stats(request: Request):
    return db.stats(_project(request))


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
    return json.loads(p.read_text(encoding="utf-8"))


@app.delete("/api/runs/{run_id}")
def api_delete(run_id: str, request: Request):
    if not authmod.can_manage(_user(request), (db.get_run(run_id) or {}).get("owner")):
        raise HTTPException(403, "Only the person who ran it (or an admin) can delete this run")
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
def library_list(request: Request):
    return db.lib_list(_project(request))


@app.post("/api/library/from-run")
def library_from_run(req: SaveScript, request: Request):
    src = ARTIFACTS / _safe(req.run_id) / "script.json"
    if not src.exists():
        raise HTTPException(404)
    t = json.loads(src.read_text(encoding="utf-8"))
    return db.lib_put(_slug(req.name or t["name"]), t, source_run=req.run_id, owner=_owner_of(request),
                      project=db.run_project(req.run_id) or _project(request))


@app.post("/api/library/upload")
async def library_upload(request: Request, file: UploadFile = File(...), name: Optional[str] = Form(None)):
    """Upload a script.json or a generated test_playwright.py / test_selenium.py (the spec is embedded)."""
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        t = extract_spec(raw)
    except Exception as e:
        raise HTTPException(400, f"Unrecognised script: {e}")
    return db.lib_put(_slug(name or t.name), json.loads(t.model_dump_json()), owner=_owner_of(request), project=_project(request))


@app.get("/api/library/{name}")
def library_get(name: str, request: Request):
    t = db.lib_get(name)
    if t is None or (db.lib_meta(name) or {}).get("project", "default") not in _projects_for(_user(request)):
        raise HTTPException(404)
    return t


@app.delete("/api/library/{name}")
def library_delete(name: str, request: Request):
    if not authmod.can_manage(_user(request), (db.lib_meta(name) or {}).get("owner")):
        raise HTTPException(403, "Only its owner or an admin can delete this script")
    db.lib_delete(name)
    return {"deleted": name}


# --------------------------------------------------------------------- API: sessions
@app.get("/api/sessions")
def sessions_list(request: Request):
    u = _user(request)
    # saved logins hold cookies: each user sees their own (admins see all)
    return db.session_list(None if not authmod.enabled() or u["role"] == "admin" else u["username"], project=_project(request))


@app.post("/api/sessions/{name}")
async def sessions_upload(name: str, request: Request, file: UploadFile = File(...)):
    """Import a Playwright storage_state.json (cookies + localStorage) as a named session."""
    try:
        state = json.loads(await file.read())
        assert isinstance(state.get("cookies", []), list)
        if not authmod.can_manage(_user(request), db.session_owner(_slug(name))):
            raise PermissionError("that name belongs to someone else's saved session")
        db.session_put(_slug(name), state, owner=_owner_of(request), project=_project(request))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(400, f"Invalid storage state: {e}")
    return {"saved": name}


@app.delete("/api/sessions/{name}")
def sessions_delete(name: str, request: Request):
    if not authmod.can_manage(_user(request), db.session_owner(name)):
        raise HTTPException(403, "Only its owner or an admin can delete this saved session")
    db.session_delete(name)
    return {"deleted": name}


# --------------------------------------------------------------------- API: AI provider
AI_KEYS = ("anthropic_key", "litellm_key")          # secrets: write-only, never returned
AI_FIELDS = ("provider", "model", "litellm_model", "litellm_base", "share_keys_with_agents", "users_may_use_org") + AI_KEYS


class AISettings(BaseModel):
    provider: Optional[str] = None          # auto | claude-code | cursor | copilot | anthropic | litellm
    model: Optional[str] = None
    litellm_model: Optional[str] = None
    litellm_base: Optional[str] = None
    anthropic_key: Optional[str] = None     # "" clears, None keeps
    litellm_key: Optional[str] = None
    share_keys_with_agents: Optional[bool] = None
    users_may_use_org: Optional[bool] = None   # people without their own AI may use this (organisation) AI


def _ai_public(cfg: dict) -> dict:
    out = {k: cfg.get(k) for k in AI_FIELDS if k not in AI_KEYS}
    out.update({f"{k}_set": bool(cfg.get(k)) for k in AI_KEYS})
    out.update({"providers": providers.report(), "status": providers.status(), "labels": providers.LABELS})
    return out


def _ai_for_agents(owner: str | None = None) -> dict:
    """What agents get with each job. A run owner with their own AI: the agent asks the hub, which answers
    with that person's subscription / key (never sent to the machine). Otherwise the organisation's choice
    (keys only if the admin opted in); the machine's own CLI login / env key is used first, then the hub."""
    scope = _ai_scope(owner)
    if scope is not None:
        return {"provider": "none"} if scope.get("provider") == "none" else {"provider": "hub"}
    cfg = _org_ai()
    return {k: v for k, v in cfg.items() if k not in AI_KEYS or cfg.get("share_keys_with_agents")}


@app.get("/api/ai")
def api_ai_get():
    return _ai_public(_org_ai())


@app.put("/api/ai")
async def api_ai_put(req: AISettings):
    cfg = _org_ai()
    for k, v in req.model_dump().items():
        if v is None:
            continue                         # not sent: keep
        if v == "" or v is False and k not in ("share_keys_with_agents", "users_may_use_org"):
            cfg.pop(k, None)
        else:
            cfg[k] = v
    if cfg.get("provider") not in (None, "auto", "none", *providers.ORDER):
        raise HTTPException(400, f"unknown provider {cfg.get('provider')!r}")
    _org_ai_save(cfg)
    providers.configure(cfg)
    await hub.broadcast({"type": "ai_changed"})
    return _ai_public(cfg)


class AICall(BaseModel):
    system: str
    user: str
    schema_: dict = Field(alias="schema")
    image_png: Optional[str] = None
    max_tokens: int = 16000
    effort: str = "low"
    run_id: Optional[str] = None             # whose run: its owner's own AI answers


@app.post("/api/ai/call")
async def api_ai_call(req: AICall, x_agent_token: Optional[str] = Header(None)):
    """For remote machines: one structured AI call answered with the hub's provider (its subscription CLI,
    key or LiteLLM), so agents in Docker or on other computers need no AI setup of their own."""
    if _agent_token(x_agent_token) is None:
        raise HTTPException(401)
    import base64
    png = base64.b64decode(req.image_png) if req.image_png else None
    run = db.get_run(req.run_id) if req.run_id else None
    if req.run_id and (not run or run.get("status") not in ("dispatched", "running")):
        raise HTTPException(403, "AI calls are answered only for runs in progress")
    owner = (run or {}).get("owner")
    answer = await asyncio.to_thread(_as_user, owner, providers.call_json, req.system, req.user, req.schema_, png,
                                     req.max_tokens, req.effort)
    return {"answer": answer, "provider": providers.active()}


@app.post("/api/ai/test")
async def api_ai_test():
    """One small real call through the active provider: proves the subscription / key / model works."""
    import time as _t
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "steps": {"type": "integer"}},
              "required": ["ok", "steps"], "additionalProperties": False}
    t0 = _t.time()
    out = await asyncio.to_thread(providers.call_json, "You check that an AI connection works.",
                                  'How many browser steps are in: "open example.com, then click Login"? Reply ok=true.', schema,
                                  None, 2000, "low")
    return {"ok": bool(out and out.get("ok")), "answer": out, "seconds": round(_t.time() - t0, 1),
            "provider": providers.active(), "status": providers.status(), "providers": providers.report()}


# --------------------------------------------------------------------- API: machines
@app.get("/api/agents")
def api_agents():
    """Every machine that ever registered, with live state for the connected ones."""
    out = []
    for m in db.machines():
        live = hub.agents.get(m["name"])
        m["online"] = bool(live)
        m["slots"] = live["slots"] if live else (m.get("info") or {}).get("slots") or 1
        m["running"] = len(live["running"]) if live else 0
        m["busy"] = bool(live and m["running"] >= m["slots"])
        m["queued"] = sum(1 for j in hub.pending if name_in(j, m["name"]))
        out.append(m)
    for g in _grids().values():      # remote browser grids behave like machines in the picker
        running = len(hub.grid_running.get(g["name"], ()))
        out.append({"name": g["name"], "kind": "grid", "online": True, "engines": [g["engine"]],
                    "browsers": [g.get("browser") or "default"], "platform": g.get("platform") or g.get("caps", {}).get("platformName") or "remote grid",
                    "hostname": _mask_url(g["url"]), "slots": g.get("slots") or 1, "running": running,
                    "busy": running >= (g.get("slots") or 1), "queued": sum(1 for j in hub.pending if g["name"] in j["targets"])})
    return out


def _grids() -> dict[str, dict]:
    return {g["name"]: g for g in db.setting_get("grids", [])}


def _mask_url(u: str) -> str:
    """https://user:KEY@hub.example.com/wd/hub -> https://user:••••••@hub.example.com/wd/hub"""
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1" + MASK + "@", u or "")


class Grid(BaseModel):
    name: str
    engine: Literal["selenium", "playwright"] = "selenium"
    url: Optional[str] = None                 # None on update = keep the stored URL (it can hold an access key)
    browser: Optional[str] = None             # browser name passed to the engine (chrome, firefox, msedge, webkit ...)
    caps: dict[str, Any] = Field(default_factory=dict)
    slots: int = 2
    platform: Optional[str] = None            # label shown in the machine picker, e.g. "Windows 11 · Chrome"


@app.get("/api/grids")
def api_grids():
    return [{**g, "url": _mask_url(g["url"])} for g in db.setting_get("grids", [])]


@app.put("/api/grids/{name}")
async def api_grid_put(name: str, g: Grid):
    name = _slug(name)
    if name in hub.agents or name in ("local", "any", LOCAL_NAME):
        raise HTTPException(409, f"'{name}' is already a machine name")
    grids = db.setting_get("grids", [])
    old = next((x for x in grids if x["name"] == name), None)
    url = g.url or (old or {}).get("url")
    if not url or not re.match(r"^(https?|wss?)://", url):
        raise HTTPException(400, "url must start with http(s):// (Selenium Grid / cloud, DevTools) or ws(s):// (Playwright server)")
    if g.engine == "selenium" and url.startswith(("ws://", "wss://")):
        raise HTTPException(400, "a Selenium grid URL is http(s)://…/wd/hub")
    entry = {**g.model_dump(), "name": name, "url": url, "slots": max(1, min(int(g.slots), 100))}
    db.setting_put("grids", [x for x in grids if x["name"] != name] + [entry])
    await hub.broadcast({"type": "agents_changed"})
    await _pump()
    return {**entry, "url": _mask_url(url)}


@app.delete("/api/grids/{name}")
async def api_grid_delete(name: str):
    db.setting_put("grids", [x for x in db.setting_get("grids", []) if x["name"] != name])
    await hub.broadcast({"type": "agents_changed"})
    return {"deleted": name}


class ProjectReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    members: Optional[list[str]] = None     # usernames; admins see every project anyway


@app.get("/api/projects")
def api_projects(request: Request):
    allowed = _projects_for(_user(request))
    out = [p for p in db.projects() if p["key"] in allowed]
    if authmod.enabled() and _user(request).get("role") != "admin":
        for p in out:
            p.pop("members", None)
    return out


@app.put("/api/projects/{key}")
def api_project_put(key: str, req: ProjectReq):
    key = _slug(key)
    old = db.project(key)
    db.project_put(key, req.name or (old or {}).get("name") or key, req.description if req.description is not None else (old or {}).get("description"))
    if req.members is not None and key != "default":
        ids = []
        for u in req.members:
            row = db.user_get(username=u)
            if not row:
                raise HTTPException(400, f"No user '{u}'")
            ids.append(row["id"])
        db.project_members_set(key, ids)
    return db.project(key)


@app.delete("/api/projects/{key}")
def api_project_delete(key: str):
    if key == "default":
        raise HTTPException(400, "The default project can't be deleted")
    db.project_delete(key)
    hub.run_project.clear()
    return {"deleted": key, "moved_to": "default"}


class TokenReq(BaseModel):
    label: str


@app.get("/api/agent-tokens")
def api_agent_tokens():
    return db.agent_tokens()


@app.post("/api/agent-tokens")
def api_agent_token_new(req: TokenReq, request: Request):
    import hashlib
    token = "apt_" + secrets.token_urlsafe(24)
    tid = db.agent_token_add(req.label.strip()[:80] or "machine", hashlib.sha256(token.encode()).hexdigest(), _owner_of(request))
    return {"id": tid, "label": req.label, "token": token}       # shown once; only its hash is stored


@app.delete("/api/agent-tokens/{tid}")
async def api_agent_token_revoke(tid: int):
    db.agent_token_revoke(tid)
    for name, a in list(hub.agents.items()):            # machines using it are disconnected now
        if a.get("token_id") == tid:
            try:
                await a["ws"].close(code=4401)
            except Exception:
                pass
    return {"revoked": tid}


def name_in(job: dict, name: str) -> bool:
    return name in job["targets"] or "any" in job["targets"]


@app.delete("/api/agents/{name}")
def api_agent_forget(name: str):
    if name in hub.agents:
        raise HTTPException(409, "Machine is connected; stop its agent first")
    db.machine_delete(name)
    return {"deleted": name}


# --------------------------------------------------------------------- dispatch
_pump_lock = asyncio.Lock()


def _candidates(job: dict) -> list[str]:
    """Machines that may take this job: its targets ("any" = every connected agent, or the hub if none),
    connected, supporting the job's engine, with a free slot - least loaded first."""
    targets = job["targets"]
    names = [n for t in targets for n in ((list(hub.agents) or ["local"]) if t == "any" else [t])]
    engine = job["config"].get("engine")
    free = []
    for n in dict.fromkeys(names):
        grids = _grids()
        if n == "local":
            used, slots = len(hub.local_running), LOCAL_WORKERS
        elif n in grids:
            if engine and engine != grids[n]["engine"]:
                continue
            used, slots = len(hub.grid_running.get(n, ())), max(1, int(grids[n].get("slots") or 1))
        elif n in hub.agents:
            a = hub.agents[n]
            if engine and a["info"].get("engines") and engine not in a["info"]["engines"]:
                continue
            used, slots = len(a["running"]), a["slots"]
        else:
            continue
        if used < slots:
            free.append((used / slots, used, n))
    return [n for *_, n in sorted(free)]


async def _pump() -> None:
    """Start every waiting job that has a free slot on one of its machines (FIFO; called on submit,
    when a run finishes and when a machine connects)."""
    async with _pump_lock:
        waiting = []
        for job in hub.pending:
            cands = _candidates(job)
            if not cands:
                waiting.append(job)
                continue
            m = cands[0]
            grid = _grids().get(m)
            if grid:
                hub.grid_running.setdefault(m, set()).add(job["run_id"])
                hub.local_runners[job["run_id"]] = []
                job["config"] = {**job["config"], "remote_url": grid["url"], "remote_caps": grid.get("caps") or {}}
                if grid.get("browser"):
                    job["config"]["browser"] = grid["browser"]
                job["machine"] = m
                db.update_run(job["run_id"], agent=m, status="dispatched", browser=job["config"].get("browser"))
                grid_pool.submit(_run_local, job)
                continue
            if m == "local":
                hub.local_running.add(job["run_id"])
                hub.local_runners[job["run_id"]] = []
                db.update_run(job["run_id"], agent=LOCAL_NAME, status="dispatched")
                pool.submit(_run_local, job)
                continue
            a = hub.agents[m]
            a["running"].add(job["run_id"])
            db.update_run(job["run_id"], agent=m, status="dispatched")
            try:
                async with a["send_lock"]:
                    await a["ws"].send_json({"type": "job", "job": {**{k: job.get(k) for k in ("run_id", "test", "config", "mode", "session_state")},
                                                                    "ai": _ai_for_agents(job.get("owner"))}})
                a["jobs"][job["run_id"]] = job
            except Exception as e:
                log.warning("could not send %s to %s: %s", job["run_id"], m, e)
                a["running"].discard(job["run_id"])
                waiting.append(job)
        hub.pending = waiting


def _local_done(run_id: str) -> None:
    hub.local_running.discard(run_id)
    hub.local_runners.pop(run_id, None)
    for s in hub.grid_running.values():
        s.discard(run_id)
    asyncio.ensure_future(_pump())


def _run_local(job: dict) -> None:
    try:
        _run_local_job(job)
    finally:
        if hub.loop:
            hub.loop.call_soon_threadsafe(_local_done, job["run_id"])


def _run_local_job(job: dict) -> None:
    try:
        test = TestCase.model_validate(job["test"])
        cfg = RunConfig.model_validate(job["config"])
        cfg.session_state = job.get("session_state")
        holder: list = hub.local_runners.setdefault(job["run_id"], [])
        # AI during the run (tie-breaks, canvas) uses the run owner's own subscription / key
        _as_user(job.get("owner"), run_test, test, cfg, ARTIFACTS, mode=job["mode"], on_event=hub.emit_threadsafe,
                 agent=job.get("machine") or LOCAL_NAME, run_id=job["run_id"], runner_out=holder)
        hub.emit_threadsafe({"type": "_finalize", "run_id": job["run_id"]})   # ordered after the run's events
        if cfg.save_session and holder and holder[0].saved_session:
            db.session_put(cfg.save_session, holder[0].saved_session, owner=job.get("owner"), project=job.get("project"))
            hub.emit_threadsafe({"type": "session_saved", "name": cfg.save_session})
    except Exception as e:
        log.exception("local run failed")
        hub.emit_threadsafe({"type": "run_finished", "run_id": job["run_id"], "status": "failed", "error": str(e)})


# --------------------------------------------------------------------- agents
@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    hello = await ws.receive_json()
    tok = _agent_token(hello.get("token"))
    if tok is None:
        await ws.send_json({"type": "error", "message": "invalid or revoked agent token (Machines → Agent tokens)"})
        await ws.close(code=4401)
        return
    name = _slug(hello.get("name") or f"agent-{len(hub.agents) + 1}")
    if name in hub.agents or name == LOCAL_NAME:
        name = f"{name}-{os.urandom(2).hex()}"
    info = {k: hello.get(k) for k in ("hostname", "platform", "engines", "browsers", "version", "slots")}
    slots = max(1, int(hello.get("slots") or 1))
    hub.agents[name] = {"ws": ws, "info": info, "slots": slots, "running": set(), "jobs": {}, "send_lock": asyncio.Lock(),
                        "token_id": tok.get("id")}
    if tok.get("id"):
        db.agent_token_used(tok["id"], name)
    db.machine_seen(name, info, online=True)
    await ws.send_json({"type": "welcome", "name": name})
    await hub.broadcast({"type": "agents_changed"})
    log.info("agent connected: %s (%d slot%s)", name, slots, "s" if slots > 1 else "")
    me = hub.agents[name]
    await _pump()                     # jobs waiting for this machine (or for any machine) can start now
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "event":
                ev = msg["event"]
                ev["agent"] = name
                await hub.broadcast(ev)
                if ev.get("type") == "run_finished":
                    me["running"].discard(ev.get("run_id"))
                    me["jobs"].pop(ev.get("run_id"), None)
                    await _pump()
            elif msg.get("type") == "session":
                try:
                    run = db.get_run(msg.get("run_id") or "") or {}
                    db.session_put(_slug(msg["name"]), msg["state"], owner=run.get("owner"), project=run.get("project"))
                    await hub.broadcast({"type": "session_saved", "name": msg["name"]})
                except Exception as e:
                    log.warning("could not save session from %s: %s", name, e)
    except WebSocketDisconnect:
        pass
    finally:
        hub.agents.pop(name, None)
        db.machine_offline(name)
        lost = sorted(me["running"])
        for rid in lost:
            await hub.broadcast({"type": "run_finished", "run_id": rid, "status": "interrupted",
                                 "error": f"Machine '{name}' disconnected"})
        await hub.broadcast({"type": "agents_changed"})
        log.info("agent disconnected: %s", name)
        await _pump()                 # "any"/pool jobs can move to the machines still connected


@app.post("/api/agents/upload/{run_id}")
async def agent_upload(run_id: str, file: UploadFile = File(...), x_agent_token: Optional[str] = Header(None)):
    if _agent_token(x_agent_token) is None:
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


# --------------------------------------------------------------------- requirements -> test design -> write-back
from . import integrations_api  # noqa: E402  (needs the hub objects above)
import sys as _sys  # noqa: E402
app.include_router(integrations_api.make_router(_sys.modules[__name__]))


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


def _iteration_labels(rows: list[tuple], sensitive: list[str]) -> list[str]:
    """'#2 HP, qty 1 · chrome · selenium · docker-1': the run number plus the data, browser, engine and machine
    that differ between the runs of a batch."""
    from ..data import flatten_items
    flat = [dict(flatten_items(r[0])) for r in rows]
    cfgs = [r[1] for r in rows]
    tgts = [" | ".join(r[2]) if len(r) > 2 else "" for r in rows]
    keys = [k for k in dict.fromkeys(k for f in flat for k in f)
            if k not in sensitive and len({str(f.get(k)) for f in flat}) > 1][:2]
    browsers_differ = len({c.browser for c in cfgs}) > 1
    engines_differ = len({c.engine for c in cfgs}) > 1
    machines_differ = len(set(tgts)) > 1
    out = []
    for i, (f, c, t) in enumerate(zip(flat, cfgs, tgts), 1):
        bits = [str(f.get(k))[:24] if j == 0 else f"{k.split('.')[-1]} {str(f.get(k))[:16]}" for j, k in enumerate(keys)]
        label = f"#{i}" + (f" {', '.join(bits)}" if bits else "")
        label += (f" · {c.browser}" if browsers_differ else "") + (f" · {c.engine}" if engines_differ else "")
        out.append(label + (f" · {t.replace('local', LOCAL_NAME)}" if machines_differ else ""))
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
