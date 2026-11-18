"""Write-back: designed cases go to the test-management tool, finished runs become executions there, and the story
gets a comment, a status transition and (on failure) a de-duplicated bug. Everything is logged per design."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path

from . import CATALOG_BY_KIND, IMPLEMENTED, make
from .base import Connector, IntegrationError
from .context import issue_of

log = logging.getLogger("aiauto.sync")

# which connection serves each role when a project has several (first configured wins)
PREFER = {"tracker": ("jira", "azure_devops", "custom"), "docs": ("confluence",), "code": ("github",),
          "tests": ("zephyr_scale", "xray", "azure_devops", "custom")}
FINAL = ("passed", "failed", "cancelled", "interrupted")


def _on(v) -> bool:
    return str(v).strip().lower() not in ("", "0", "no", "off", "false", "none")


def connector(db, project: str, kind: str) -> Connector | None:
    c = db.connection_get(project, kind)
    if not c or not _on(c["settings"].get("enabled", "yes")) or kind not in IMPLEMENTED:
        return None
    return make(kind, c["settings"], c["secrets"])


def for_role(db, project: str, role: str, all_: bool = False):
    """The connection(s) a project uses for a role: tracker / docs / code / tests."""
    have = {c["kind"]: c for c in db.connections(project)}
    kinds = [k for k in PREFER[role] if k in have] + [k for k in have if k not in PREFER[role] and
                                                       role in CATALOG_BY_KIND.get(k, {}).get("roles", ())]
    out = []
    for k in kinds:
        if role not in CATALOG_BY_KIND.get(k, {}).get("roles", ()):
            continue
        if role == "tests" and k in ("azure_devops", "custom") and not _on(have[k]["settings"].get("record_runs", "yes")):
            continue
        try:
            c = connector(db, project, k)
        except IntegrationError as e:
            log.info("%s not usable: %s", k, e)
            continue
        if c:
            out.append(c)
            if not all_:
                break
    return out if all_ else (out[0] if out else None)


def _log(db, design: dict, kind: str, action: str, ok: bool, detail: str, ref: str | None = None) -> None:
    db.sync_log_add(design["id"], design["project"], kind, action, ok, detail, ref)


def public_url() -> str:
    return (os.getenv("AIAUTO_PUBLIC_URL") or "").rstrip("/")


def report_link(run_id: str) -> str:
    base = public_url()
    return f"{base}/artifacts/{run_id}/report.html" if base else f"/artifacts/{run_id}/report.html"


# ---------------------------------------------------------------------------------- cases -> test tool
def push_design(db, design: dict, only: list[str] | None = None) -> dict:
    """Create or update the approved cases in the test-management tool. Returns {case id: external key}."""
    tool = for_role(db, design["project"], "tests")
    if not tool:
        raise IntegrationError("No test-management tool is connected for this project (Admin → Integrations)")
    issue = issue_of(design["pack"])
    out = {}
    for c in design["cases"]:
        if c.get("status") != "approved" or (only and c["id"] not in only):
            continue
        try:
            key = tool.push_case(c, issue)
            if key:
                c.setdefault("external", {})[tool.kind] = key
            out[c["id"]] = key
            _log(db, design, tool.kind, "push case", True, c["title"], key)
        except IntegrationError as e:
            out[c["id"]] = None
            _log(db, design, tool.kind, "push case", False, f"{c['title']}: {e}")
    db.design_put(design)
    return out


# ---------------------------------------------------------------------------------- a launch (one cycle)
def start_launch(db, design: dict, run_case: dict[str, str]) -> str:
    """Record a launch: which run ran which case; opens a test cycle (one per launch) in the tool."""
    launch = uuid.uuid4().hex[:8]
    for rid, cid in run_case.items():
        db.design_run_add(rid, design["id"], cid, launch)
    entry = {"started": time.time(), "runs": list(run_case), "cycle": {}, "done": False}
    tool = None
    try:
        tool = for_role(db, design["project"], "tests")
    except IntegrationError:
        pass
    if tool:
        name = f"Autopilot · {design['issue_key']} · {time.strftime('%Y-%m-%d %H:%M')}"
        try:
            entry["cycle"][tool.kind] = tool.start_cycle(name, issue_of(design["pack"]))
            _log(db, design, tool.kind, "start cycle", True, name, entry["cycle"][tool.kind])
        except IntegrationError as e:
            _log(db, design, tool.kind, "start cycle", False, str(e))
    fresh = db.design_get(design["id"]) or design
    fresh.setdefault("cycles", {})[launch] = entry
    db.design_put(fresh)
    return launch


def _result(summary: dict, run_id: str) -> dict:
    res = summary.get("results") or []
    bad = next((r for r in res if r.get("status") == "failed"), None)
    status = summary.get("status") or ("failed" if bad else "passed")
    lines = [f"Autopilot run {run_id}: {status} ({len([r for r in res if r.get('status') == 'passed'])}/{len(res)} steps passed)",
             f"Report: {report_link(run_id)}"]
    if bad:
        lines.append(f"Failed at step {bad['index'] + 1}: {(bad.get('step') or {}).get('raw', '')} — {bad.get('error', '')}")
    return {"status": status, "duration_ms": summary.get("duration_ms") or 0, "comment": "\n".join(lines),
            "steps": [{"status": r.get("status"), "error": r.get("error")} for r in res], "failed": bad}


def record_run(db, run_id: str, artifacts: Path) -> None:
    """Called once a run that belongs to a design has finished: execution, bug, and maybe the story update."""
    link = db.design_run(run_id)
    if not link or link["synced"]:
        return
    design = db.design_get(link["design_id"])
    p = Path(artifacts) / run_id / "summary.json"
    if not design or not p.exists():
        db.design_run_synced(run_id)
        return
    summary = json.loads(p.read_text(encoding="utf-8"))
    if summary.get("status") not in FINAL:
        return
    case = next((c for c in design["cases"] if c["id"] == link["case_id"]), None)
    db.design_run_synced(run_id)
    if not case:
        return
    issue = issue_of(design["pack"])
    result = _result(summary, run_id)
    launch = (design.get("cycles") or {}).get(link["launch_id"]) or {}

    tool = for_role(db, design["project"], "tests")
    if tool and result["status"] in ("passed", "failed", "interrupted"):
        try:
            cycle = (launch.get("cycle") or {}).get(tool.kind) or ""
            if not cycle and tool.kind not in ("xray",):
                raise IntegrationError("no test cycle was opened for this launch")
            ref = tool.record_execution(cycle, case, result, issue)
            if tool.kind == "xray" and not case.get("external", {}).get("xray"):
                pass   # Xray created the test on import; its key is looked up by the next push (summary match)
            _log(db, design, tool.kind, "record execution", True, f"{case['title']}: {result['status']}", ref)
        except IntegrationError as e:
            _log(db, design, tool.kind, "record execution", False, f"{case['title']}: {e}")

    tracker = for_role(db, design["project"], "tracker")
    if tracker and result["status"] == "failed":
        _bug(db, design, tracker, case, result, issue, run_id, Path(artifacts) / run_id)

    # the launch is complete: close the cycle, comment on the story, move it
    runs = db.design_runs(design["id"], link["launch_id"])
    if runs and all(r["synced"] for r in runs):
        fresh = db.design_get(design["id"]) or design
        entry = fresh.get("cycles", {}).get(link["launch_id"])
        if entry and not entry.get("done"):
            entry["done"] = True
            db.design_put(fresh)
            _finish_launch(db, fresh, runs, entry, tool, tracker, issue)


def _bug(db, design, tracker, case, result, issue, run_id, run_dir: Path) -> None:
    conn = db.connection_get(design["project"], tracker.kind) or {"settings": {}}
    if not _on(conn["settings"].get("create_bugs", "yes")):
        return
    bad = result["failed"] or {}
    sig = f"{case['id']}|{bad.get('index')}|{(bad.get('error') or '')[:120]}"
    label = "autopilot-" + hashlib.sha1(sig.encode()).hexdigest()[:10]
    try:
        existing = tracker.find_bug(label)
        if existing:
            tracker.comment(existing, f"Still failing in Autopilot run {run_id}.\nReport: {report_link(run_id)}")
            _log(db, design, tracker.kind, "bug (seen again)", True, case["title"], existing)
            return
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(case.get("steps") or [], 1))
        desc = (f"Found by Autopilot while testing {issue.key} ({issue.title}).\n\nTest case: {case['title']}\n\nSteps:\n{steps}\n\n"
                f"Expected: {case.get('expected') or 'the checks pass'}\n"
                f"Actual: step {bad.get('index', 0) + 1} failed: {bad.get('error', '')}\n\nReport: {report_link(run_id)}")
        key = tracker.create_bug(f"[Autopilot] {case['title']}", desc, issue, ["autopilot", label])
        shot = bad.get("screenshot")
        if key and shot and (run_dir / shot).is_file():
            try:
                tracker.attach(key, Path(shot).name, (run_dir / shot).read_bytes())
            except IntegrationError:
                pass
        _log(db, design, tracker.kind, "create bug", True, case["title"], key)
    except (IntegrationError, NotImplementedError) as e:
        _log(db, design, tracker.kind, "create bug", False, f"{case['title']}: {e}")


def latest_by_case(db, design: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in db.design_runs(design["id"]):         # newest first
        if r["case_id"] not in out and r.get("status") in FINAL:
            out[r["case_id"]] = r
    return out


def _finish_launch(db, design, runs, entry, tool, tracker, issue) -> None:
    if tool and hasattr(tool, "finish_cycle") and (entry.get("cycle") or {}).get(tool.kind):
        try:
            tool.finish_cycle(entry["cycle"][tool.kind])
        except IntegrationError as e:
            _log(db, design, tool.kind, "finish cycle", False, str(e))
    if not tracker:
        return
    passed = sum(1 for r in runs if r.get("status") == "passed")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    other = len(runs) - passed - failed
    titles = {c["id"]: c["title"] for c in design["cases"]}
    lines = [f"Autopilot ran {len(runs)} test case(s) for {issue.key}: {passed} passed, {failed} failed" +
             (f", {other} not finished" if other else "") + "."]
    if entry.get("cycle"):
        lines.append("Test cycle: " + ", ".join(f"{k} {v}" for k, v in entry["cycle"].items()))
    for r in sorted(runs, key=lambda r: r.get("status") != "failed"):
        lines.append(f"- {r.get('status', '?').upper()}: {titles.get(r['case_id'], r['case_id'])} — {report_link(r['run_id'])}")
    try:
        tracker.comment(issue.key, "\n".join(lines))
        _log(db, design, tracker.kind, "comment", True, lines[0], issue.key)
    except (IntegrationError, NotImplementedError) as e:
        _log(db, design, tracker.kind, "comment", False, str(e))
    # status: every approved case's latest run passed -> pass transition; any failed -> fail transition
    conn = (db.connection_get(design["project"], tracker.kind) or {"settings": {}})["settings"]
    approved = [c["id"] for c in design["cases"] if c.get("status") == "approved"]
    latest = latest_by_case(db, design)
    states = [latest[c]["status"] for c in approved if c in latest]
    target = None
    if states and len(states) == len(approved) and all(s == "passed" for s in states):
        target = conn.get("pass_transition")
    elif "failed" in states:
        target = conn.get("fail_transition")
    if target:
        try:
            got = tracker.transition(issue.key, target)
            _log(db, design, tracker.kind, "transition", True, f"{issue.key} → {got}", issue.key)
        except (IntegrationError, NotImplementedError) as e:
            _log(db, design, tracker.kind, "transition", False, str(e))
