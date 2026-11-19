"""Hub API with SQLite: iterations with per-row browser, run history, files, export, replay with the same
secrets, and persistence across a restart."""
import importlib
import io
import json
import time
import zipfile

import pytest

from .conftest import EXAMPLES


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAUTO_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("AIAUTO_ARTIFACTS", str(tmp_path / "artifacts"))
    import aiauto.server.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        yield c, appmod


def _wait(c, ids, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        runs = {r["run_id"]: r for r in c.get("/api/runs").json()}
        if all(runs.get(i, {}).get("status") in ("passed", "failed", "interrupted") for i in ids):
            return runs
        time.sleep(0.5)
    raise AssertionError("runs did not finish")


def test_hub_flow(hub, shop_url):
    c, appmod = hub
    body = {"text": (EXAMPLES / "shop_cart.txt").read_text(), "name": "Cart flow",
            "data": {"base_url": shop_url, "product": "wireless mouse"},
            "iterations": [{"data": {"brand": "Logitech", "qty": "2", "token": "tok-1"}, "browser": "chromium"},
                           {"data": {"brand": "HP", "qty": "1", "token": "tok-2"}, "browser": "chromium"}],
            "sensitive": ["token"], "config": {"engine": "playwright", "record_video": False, "use_llm": False, "timeout_ms": 20000}, "agent": "local"}
    r = c.post("/api/runs", json=body)
    assert r.status_code == 200, r.text
    ids = r.json()["run_ids"]
    assert len(ids) == 2 and r.json()["batch_id"]
    runs = _wait(c, ids)
    assert all(runs[i]["status"] == "passed" and runs[i]["mode"] == "ai" for i in ids), \
        [(runs[i]["status"], runs[i].get("error"), [(st["idx"], st["error"]) for st in c.get(f"/api/runs/{i}").json()["steps"] if st["error"]]) for i in ids]

    d = c.get(f"/api/runs/{ids[0]}").json()
    assert d["data"]["token"] == "••••••" and d["has_secrets"] and len(d["batch"]) == 2 and d["steps"]
    files = [f["path"] for f in c.get(f"/api/runs/{ids[0]}/files").json()]
    assert {"test_playwright.py", "test_selenium.py", "script.json", "report.html"} <= set(files)

    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/{ids[0]}/export").content))
    names = z.namelist()
    root = names[0].split("/")[0]
    assert {f"{root}/run.py", f"{root}/tests/test_selenium.py", f"{root}/tests/autopilot_runtime.py",
            f"{root}/data/iteration_2.json", f"{root}/.env.example"} <= set(names)
    assert "imageio-ffmpeg" in z.read(f"{root}/requirements.txt").decode()
    assert "RESULTS_DIR" in z.read(f"{root}/run.py").decode()
    assert "AIAUTO_SECRET_TOKEN=" in z.read(f"{root}/.env.example").decode()
    assert "tok-1" not in b"".join(z.read(n) for n in names).decode(errors="ignore")

    # replay the generated script with the same data (secret comes from the hub vault) and with new data
    rr = c.post("/api/runs", json={"replay_run_id": ids[0], "config": {"engine": "selenium", "record_video": False}, "agent": "local"}).json()
    rn = c.post("/api/runs", json={"replay_run_id": ids[0], "data": {"brand": "HP", "qty": "1"},
                                   "config": {"engine": "playwright", "record_video": False}, "agent": "local"}).json()
    runs = _wait(c, rr["run_ids"] + rn["run_ids"])
    for rid in rr["run_ids"] + rn["run_ids"]:
        assert runs[rid]["status"] == "passed" and runs[rid]["mode"] == "replay"
    assert c.get(f"/api/runs/{rr['run_ids'][0]}").json()["has_secrets"]

    # restart: a fresh DB handle on the same file still has every run, nothing left "running"
    from aiauto.db import DB
    again = DB(appmod.db.path)
    again.recover()
    assert {r["run_id"] for r in again.list_runs()} >= set(ids + rr["run_ids"] + rn["run_ids"])


def test_reporting_views_and_practice_pages(hub, tmp_path, monkeypatch):
    """The report_* views read the runs back as plain rows; the hub serves the practice pages at /demo/."""
    c, appmod = hub
    assert c.get("/demo/").status_code == 200 and "DemoShop" in c.get("/demo/").text
    assert c.get("/demo/shop/index.html").status_code == 200
    from aiauto.db import DB, VIEWS
    db = DB(tmp_path / "views.db")
    db.create_run({"run_id": "r1", "test_name": "Login", "status": "failed", "engine": "selenium", "browser": "chrome",
                   "agent": "local", "mode": "ai", "total": 2, "parser": "rules", "owner": "tess", "project": "shop"})
    db.on_event({"type": "step_finished", "run_id": "r1", "index": 0, "status": "passed", "raw": "Click Login",
                 "action": "click", "locator": "role=button:Login", "duration_ms": 40, "picked_by": "heuristic"})
    db.on_event({"type": "step_finished", "run_id": "r1", "index": 1, "status": "failed", "raw": 'Verify page contains "Hi"',
                 "action": "assert_text", "error": "StepError: not found", "duration_ms": 900})
    db.on_event({"type": "run_finished", "run_id": "r1", "status": "failed", "error": "StepError: not found"})
    db.create_run({"run_id": "r2", "test_name": "Login", "status": "passed", "engine": "playwright", "mode": "replay",
                   "project": "shop", "total": 2})
    assert set(VIEWS) == {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='view'")}
    run = db.one("SELECT * FROM report_runs WHERE run_id='r1'")
    assert run["project"] == "shop" and run["mode"] == "ai" and run["steps_passed"] == 1 and run["steps_failed"] == 1
    assert db.one("SELECT mode FROM report_runs WHERE run_id='r2'")["mode"] == "script"
    steps = db.q("SELECT step, status, picked_by FROM report_steps WHERE run_id='r1' ORDER BY step")
    assert [(s["step"], s["status"]) for s in steps] == [(1, "passed"), (2, "failed")] and steps[0]["picked_by"] == "heuristic"
    t = db.one("SELECT * FROM report_tests WHERE test_name='Login'")
    assert (t["runs"], t["passed"], t["failed"], t["pass_rate_pct"]) == (2, 1, 1, 50.0)
    assert db.one("SELECT COUNT(*) n FROM report_failures")["n"] == 1
    assert db.one("SELECT runs FROM report_daily WHERE project='shop'")["runs"] == 2
    DB(tmp_path / "views.db")                     # re-created on every start without trouble

    monkeypatch.setenv("AIAUTO_DEMO_SITE", "off")  # and the practice pages can be turned off
    off = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(off.app) as c2:
        assert "DemoShop" not in c2.get("/demo/").text


def test_demo_data_refuses_a_database_with_accounts(tmp_path, capsys):
    from aiauto.db import DB
    from aiauto import demo
    db = DB(tmp_path / "team.db")
    db.user_create("alice", "admin", "x")
    with pytest.raises(SystemExit, match="already has accounts"):
        demo.main(["--db", str(tmp_path / "team.db"), "--artifacts", str(tmp_path / "a")])
    with pytest.raises(SystemExit, match="at least 10"):
        demo.main(["--db", str(tmp_path / "new.db"), "--password", "short"])


def test_reviewed_steps_are_named_from_what_they_do(hub):
    """Running the breakdown as is ('Break down into steps' then Run) names the run like parsed text; edited steps
    stay 'Edited test' unless named."""
    c, appmod = hub
    from aiauto.nlp.rules import parse
    steps, _ = parse('Open http://localhost:1/shop\nType "mouse" into Search\nPress Enter')
    body = {"steps": [s.model_dump() for s in steps], "text": "x", "agent": "local", "config": {"record_video": False, "timeout_ms": 1000}}
    rid = c.post("/api/runs", json=body).json()["run_ids"][0]
    assert c.get(f"/api/runs/{rid}").json()["test_name"] != "Edited test"
    edited = [{**s.model_dump(), "source": "user"} for s in steps]
    rid2 = c.post("/api/runs", json={**body, "steps": edited}).json()["run_ids"][0]
    assert c.get(f"/api/runs/{rid2}").json()["test_name"] == "Edited test"
    c.post("/api/runs/cancel", json={"run_ids": [rid, rid2]})
