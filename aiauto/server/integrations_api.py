"""Hub API for the requirements → test design → run → write-back flow.

  /api/integrations/catalog           what can be connected (standard / under development / planned)
  /api/connections[/{kind}[/test]]    a project's connections; tokens are write-only (admin)
  /api/requirements?q= , /{key}       search the tracker; the context pack for one story
  /api/designs ...                    generate, review/edit, approve, push to the test tool, run, sync log
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..integrations import CATALOG, CATALOG_BY_KIND, IMPLEMENTED, SECRET_FIELDS, IntegrationError, make
from ..integrations import context as ctx
from ..integrations import design as designer
from ..integrations import sync
from ..models import RunConfig

log = logging.getLogger("aiauto.integrations")
MASKED = "••••••••"
# one writer thread: executions, bugs and the story update for a design are applied in order
sync_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")
io_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="integrations")


class ConnectionBody(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Optional[str]] = Field(default_factory=dict)    # blank / masked = keep the saved one


class PastedStory(BaseModel):
    text: str                                   # Markdown, Jira wiki markup or plain text
    title: str = ""
    key: str = ""                               # its tracker key if it has one (finds commits that mention it)
    type: str = "Story"                         # Story | Bug
    notes: str = ""                             # spec / wiki page text pasted next to it


class DesignRequest(BaseModel):
    issue_key: Optional[str] = None             # from the tracker
    story: Optional[PastedStory] = None         # or pasted by hand (works with no tracker connected)
    use_ai: bool = False


class LinkRequest(BaseModel):
    issue_key: str


class DesignSettings(BaseModel):
    auto_sync: bool                             # send results after every run (else they wait for Publish)


class DesignUpdate(BaseModel):
    cases: list[dict]
    notes: Optional[list[str]] = None


class CaseStatus(BaseModel):
    case_ids: list[str]
    status: str = "approved"            # approved | rejected | draft


class DesignRun(BaseModel):
    case_ids: Optional[list[str]] = None          # default: every approved case
    base_url: str
    data: dict[str, Any] = Field(default_factory=dict)       # extra / overriding values (e.g. real test accounts)
    sensitive: list[str] = Field(default_factory=list)
    config: RunConfig = Field(default_factory=RunConfig)
    agent: Union[str, list[str]] = "local"
    engines: Optional[list[str]] = None


def make_router(app_mod) -> APIRouter:
    """app_mod: the hub module (db, hub, helpers); passed in so this file stays free of import cycles."""
    r = APIRouter()
    db = app_mod.db

    def project(request: Request) -> str:
        return app_mod._project(request)

    async def run_io(fn, *a):
        try:
            return await asyncio.get_running_loop().run_in_executor(io_pool, fn, *a)
        except IntegrationError as e:
            raise HTTPException(e.status if e.status in (400, 401, 403, 404, 409, 422) else 502, str(e))
        except NotImplementedError:
            raise HTTPException(400, "That tool can't do this")

    def design_of(request: Request, design_id: str) -> dict:
        d = db.design_get(design_id)
        if not d or d["project"] not in app_mod._projects_for(app_mod._user(request)):
            raise HTTPException(404, "No such design")
        return d

    def need(request: Request, role: str):
        try:
            c = sync.for_role(db, project(request), role)
        except IntegrationError as e:
            raise HTTPException(400, str(e))
        if not c:
            names = " or ".join(CATALOG_BY_KIND[k]["label"] for k in sync.PREFER[role] if k in CATALOG_BY_KIND)
            raise HTTPException(400, f"Connect {names} for this project first (Admin → Integrations)")
        return c

    # ------------------------------------------------------------------ catalog / connections
    @r.get("/api/integrations/catalog")
    def catalog(request: Request):
        have = {c["kind"] for c in db.connections(project(request))}
        return [{**c, "available": c["kind"] in IMPLEMENTED, "configured": c["kind"] in have} for c in CATALOG]

    def public(c: dict) -> dict:
        secrets = SECRET_FIELDS.get(c["kind"], [])
        return {"kind": c["kind"], "settings": c["settings"], "secrets": {k: MASKED if c["secrets"].get(k) else "" for k in secrets},
                "updated": c["updated"], "updated_by": c["updated_by"], "label": CATALOG_BY_KIND.get(c["kind"], {}).get("label", c["kind"])}

    @r.get("/api/connections")
    def connections(request: Request):
        return [public(c) for c in db.connections(project(request))]

    def merged(request: Request, kind: str, body: ConnectionBody | None) -> tuple[dict, dict]:
        if kind not in CATALOG_BY_KIND:
            raise HTTPException(404, f"Unknown integration '{kind}'")
        if kind not in IMPLEMENTED:
            raise HTTPException(400, f"{CATALOG_BY_KIND[kind]['label']} is planned, not available yet")
        cur = db.connection_get(project(request), kind) or {"settings": {}, "secrets": {}}
        if body is None:
            return cur["settings"], cur["secrets"]
        allowed = {f["key"] for f in CATALOG_BY_KIND[kind]["fields"]} | {"enabled"}
        settings = {k: v for k, v in body.settings.items() if k in allowed and k not in SECRET_FIELDS[kind]}
        secrets = dict(cur["secrets"])
        for k, v in body.secrets.items():
            if k not in SECRET_FIELDS[kind]:
                continue
            if v is None or v == "":
                if k in body.secrets and v is None:
                    secrets.pop(k, None)          # explicit null: forget it
                continue
            if v != MASKED:
                secrets[k] = v.strip()
        return settings, secrets

    @r.put("/api/connections/{kind}")
    def connection_put(kind: str, body: ConnectionBody, request: Request):
        settings, secrets = merged(request, kind, body)
        try:
            make(kind, settings, secrets)          # validates required settings (no network call)
        except IntegrationError as e:
            raise HTTPException(400, str(e))
        db.connection_put(project(request), kind, settings, secrets, app_mod._owner_of(request))
        return public(db.connection_get(project(request), kind))

    @r.delete("/api/connections/{kind}")
    def connection_delete(kind: str, request: Request):
        db.connection_delete(project(request), kind)
        return {"ok": True}

    @r.post("/api/connections/{kind}/test")
    async def connection_test(kind: str, request: Request, body: Optional[ConnectionBody] = None):
        settings, secrets = merged(request, kind, body)
        try:
            c = make(kind, settings, secrets)
        except IntegrationError as e:
            return {"ok": False, "message": str(e)}
        try:
            msg = await asyncio.get_running_loop().run_in_executor(io_pool, c.test)
            return {"ok": True, "message": msg}
        except IntegrationError as e:
            return {"ok": False, "message": str(e)}

    # ------------------------------------------------------------------ requirements
    @r.get("/api/requirements")
    async def requirements(request: Request, q: str = "", limit: int = 25):
        tracker = need(request, "tracker")
        found = await run_io(tracker.search, q, min(max(limit, 1), 100))
        designed = {d["issue_key"]: d for d in db.designs(project(request))}
        return [{**i.to_dict(), "design": designed.get(i.key)} for i in found]

    def build_pack(proj: str, key: str) -> dict:
        tracker = sync.for_role(db, proj, "tracker")
        if not tracker:
            raise IntegrationError("No tracker connected for this project", 400)
        return ctx.build(tracker, key, sync.for_role(db, proj, "docs", all_=True), sync.for_role(db, proj, "code", all_=True))

    @r.get("/api/requirements/{key}")
    async def requirement(key: str, request: Request):
        need(request, "tracker")
        pack = await run_io(build_pack, project(request), key)
        return {"pack": pack, "designs": db.designs(project(request), key)}

    # ------------------------------------------------------------------ designs
    @r.post("/api/designs")
    async def design_create(body: DesignRequest, request: Request):
        proj, owner = project(request), app_mod._owner_of(request)
        if body.story:
            st = body.story
            if not st.text.strip():
                raise HTTPException(400, "Paste the story first")
            issue = ctx.issue_from_text(st.text, st.title, st.key, st.type)
            pack = await run_io(lambda: ctx.build_for(issue, sync.for_role(db, proj, "docs", all_=True),
                                                      sync.for_role(db, proj, "code", all_=True), st.notes))
        elif body.issue_key:
            need(request, "tracker")
            pack = await run_io(build_pack, proj, body.issue_key.strip())
        else:
            raise HTTPException(400, "Give an issue key or paste a story")
        out = await asyncio.get_running_loop().run_in_executor(
            io_pool, app_mod._as_user, owner, designer.design, pack, body.use_ai)
        d = {"id": uuid.uuid4().hex[:12], "project": proj, "issue_key": pack["issue"]["key"], "issue_title": pack["issue"]["title"],
             "status": "draft", "pack": pack, "cases": out["cases"], "notes": out["notes"], "created_by": owner, "cycles": {}}
        db.design_put(d)
        db.sync_log_add(d["id"], proj, "autopilot", "design", True,
                        f"{len(out['cases'])} cases from {'rules + AI' if body.use_ai else 'rules'}", d["issue_key"], owner)
        return view(db.design_get(d["id"]))

    def view(d: dict) -> dict:
        unpublished = sum(1 for x in db.design_runs(d["id"]) if not x["synced"] and x.get("status") in sync.FINAL)
        return {**d, "coverage": designer.coverage(d["pack"], d["cases"]), "latest": sync.latest_by_case(db, d),
                "auto_sync": sync.auto_sync(d), "linked": sync.linked(d), "unpublished": unpublished}

    @r.get("/api/designs")
    def designs(request: Request, issue_key: Optional[str] = None):
        return db.designs(project(request), issue_key)

    @r.get("/api/designs/{design_id}")
    def design_get(design_id: str, request: Request):
        return view(design_of(request, design_id))

    @r.put("/api/designs/{design_id}")
    def design_put(design_id: str, body: DesignUpdate, request: Request):
        d = design_of(request, design_id)
        old = {c["id"]: c for c in d["cases"]}
        cases = []
        for c in body.cases:
            c = {**designer._case(c.get("title") or "Untitled", c.get("type") or "positive", c.get("steps") or [], origin="user"),
                 **{k: v for k, v in c.items() if k in ("id", "title", "type", "priority", "ac", "source", "preconditions", "steps",
                                                        "expected", "data", "sensitive", "origin", "status", "external", "notes", "covers")}}
            c["steps"] = [str(s).strip() for s in c["steps"] if str(s).strip()]
            prev = old.get(c["id"])
            if prev and (prev["steps"] != c["steps"] or prev["title"] != c["title"]) and prev.get("origin") != "user":
                c["origin"] = "edited"
                if c.get("status") == "approved" and prev["steps"] != c["steps"]:
                    c["status"] = "draft"           # an approved case changed: approve it again
            cases.append(c)
        d["cases"] = designer.check(cases)
        if body.notes is not None:
            d["notes"] = body.notes
        db.design_put(d)
        return view(db.design_get(design_id))

    @r.delete("/api/designs/{design_id}")
    def design_delete(design_id: str, request: Request):
        d = design_of(request, design_id)
        if not app_mod.authmod.can_manage(app_mod._user(request), d.get("created_by")):
            raise HTTPException(403, "Only its author or an admin can delete a design")
        db.design_delete(design_id)
        return {"ok": True}

    @r.post("/api/designs/{design_id}/status")
    def design_status(design_id: str, body: CaseStatus, request: Request):
        if body.status not in ("approved", "rejected", "draft"):
            raise HTTPException(400, "status must be approved, rejected or draft")
        d = design_of(request, design_id)
        ids = set(body.case_ids)
        blocked = []
        for c in d["cases"]:
            if c["id"] in ids:
                if body.status == "approved" and any(n.startswith(("Step not understood", "No value for")) for n in c.get("notes") or []):
                    blocked.append(c["title"])
                    continue
                c["status"] = body.status
        approved = sum(1 for c in d["cases"] if c.get("status") == "approved")
        d["status"] = "approved" if approved else "draft"
        db.design_put(d)
        out = view(db.design_get(design_id))
        out["blocked"] = blocked
        return out

    @r.post("/api/designs/{design_id}/refresh")
    async def design_refresh(design_id: str, request: Request):
        """Re-read the story/docs/code: says whether anything changed since the cases were written."""
        d = design_of(request, design_id)
        meta = d["pack"].get("meta") or {}
        if d["pack"]["issue"].get("source") == "pasted" or meta.get("linked_from") == "pasted":
            # the pasted text is the story: re-read only its docs and code
            notes = next((x["text"] for x in d["pack"].get("docs") or [] if x.get("pasted")), "")
            proj = d["project"]
            pack = await run_io(lambda: ctx.build_for(ctx.issue_of(d["pack"]), sync.for_role(db, proj, "docs", all_=True),
                                                      sync.for_role(db, proj, "code", all_=True), notes, meta))
        else:
            pack = await run_io(build_pack, d["project"], d["issue_key"])
        changed = pack["fingerprint"] != d["pack"].get("fingerprint")
        if changed:
            d["pack"], d["notes"] = pack, list(dict.fromkeys((d.get("notes") or []) +
                                                            ["The story, its docs or its code changed after these cases were written"]))
            d["status"] = "stale" if d["status"] != "draft" else "draft"
            db.design_put(d)
        return {"changed": changed, "design": view(db.design_get(design_id))}

    @r.post("/api/designs/{design_id}/push")
    async def design_push(design_id: str, request: Request):
        d = design_of(request, design_id)
        if not any(c.get("status") == "approved" for c in d["cases"]):
            raise HTTPException(400, "Approve some cases first")
        need(request, "tests")
        got = await asyncio.get_running_loop().run_in_executor(sync_pool, sync.as_actor, app_mod._owner_of(request), sync.push_design, db, d)
        return {"pushed": got, "design": view(db.design_get(design_id))}

    @r.post("/api/designs/{design_id}/run")
    async def design_run(design_id: str, body: DesignRun, request: Request):
        from ..nlp import rules as rule_parser
        d = design_of(request, design_id)
        want = set(body.case_ids or [])
        cases = [c for c in d["cases"] if c.get("status") == "approved" and (not want or c["id"] in want)]
        if not cases:
            raise HTTPException(400, "No approved cases to run")
        if not body.base_url.strip():
            raise HTTPException(400, "Set the URL of the app under test")
        tool = sync.for_role(db, d["project"], "tests")
        if tool and sync.auto_sync(d) and tool.kind == "zephyr_scale" and any(not (c.get("external") or {}).get(tool.kind) for c in cases):
            await asyncio.get_running_loop().run_in_executor(sync_pool, sync.as_actor, app_mod._owner_of(request), sync.push_design, db, d,
                                                       [c["id"] for c in cases])
            d = db.design_get(design_id)
            cases = [c for c in d["cases"] if c.get("status") == "approved" and (not want or c["id"] in want)]
        run_case, skipped = {}, []
        for c in cases:
            steps, unparsed = rule_parser.parse("\n".join(c["steps"]))
            if unparsed or not steps:
                skipped.append({"case": c["title"], "unparsed": unparsed})
                continue
            data = {**(c.get("data") or {}), **body.data, "base_url": body.base_url.rstrip("/")}
            req = app_mod.RunRequest(steps=steps, name=c["title"], data=data, sensitive=sorted(set(c.get("sensitive") or []) | set(body.sensitive)),
                                     config=body.config.model_copy(), agent=body.agent, engines=body.engines)
            got = await app_mod.api_run(req, request)
            for rid in got["run_ids"]:
                run_case[rid] = c["id"]
        if not run_case:
            raise HTTPException(422, {"message": "None of the cases could be run", "skipped": skipped})
        launch = await asyncio.get_running_loop().run_in_executor(sync_pool, sync.as_actor, app_mod._owner_of(request), sync.start_launch, db, d, run_case)
        return {"launch": launch, "run_ids": list(run_case), "skipped": skipped}

    @r.post("/api/designs/{design_id}/publish")
    async def design_publish(design_id: str, request: Request):
        """Local work -> the tools: cases, the latest result of each, the story comment / transition / bugs."""
        d = design_of(request, design_id)
        try:
            out = await asyncio.get_running_loop().run_in_executor(sync_pool, sync.as_actor, app_mod._owner_of(request), sync.publish, db, d, app_mod.ARTIFACTS)
        except IntegrationError as e:
            raise HTTPException(e.status if e.status in (400, 401, 403, 404) else 502, str(e))
        return {**out, "design": view(db.design_get(design_id))}

    @r.put("/api/designs/{design_id}/settings")
    def design_settings(design_id: str, body: DesignSettings, request: Request):
        d = design_of(request, design_id)
        sync.as_actor(app_mod._owner_of(request), sync.set_auto_sync, db, d, body.auto_sync)
        return view(db.design_get(design_id))

    @r.post("/api/designs/{design_id}/link")
    async def design_link(design_id: str, body: LinkRequest, request: Request):
        d = design_of(request, design_id)
        try:
            await asyncio.get_running_loop().run_in_executor(sync_pool, sync.as_actor, app_mod._owner_of(request), sync.link_story, db, d, body.issue_key)
        except IntegrationError as e:
            raise HTTPException(e.status if e.status in (400, 401, 403, 404) else 502, str(e))
        return view(db.design_get(design_id))

    @r.get("/api/designs/{design_id}/runs")
    def design_runs(design_id: str, request: Request):
        design_of(request, design_id)
        return db.design_runs(design_id)

    @r.get("/api/designs/{design_id}/log")
    def design_log(design_id: str, request: Request):
        design_of(request, design_id)
        return db.sync_log(design_id)

    @r.get("/api/sync-log")
    def project_log(request: Request):
        return db.sync_log(project=project(request))

    return r


def on_run_finished(db, run_id: str, artifacts) -> None:
    """Hook from the hub once a run's summary is in the DB: queue its write-back (no-op for other runs)."""
    if not db.design_run(run_id):
        return

    who = (db.get_run(run_id) or {}).get("owner")

    def go():
        try:
            sync.as_actor(f"{who} (run)" if who else None, sync.record_run, db, run_id, artifacts)
        except Exception as e:      # never let a tracker outage break the hub
            log.warning("write-back for %s failed: %s", run_id, e)
            link = db.design_run(run_id) or {}
            d = db.design_get(link.get("design_id") or "") if link else None
            if d:
                db.sync_log_add(d["id"], d["project"], "autopilot", "write-back", False, f"{type(e).__name__}: {e}", run_id, who)
    sync_pool.submit(go)
