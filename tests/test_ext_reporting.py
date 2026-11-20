"""Runs the Chrome extension reports to the hub: the run record, live events, screenshots, video, report, script and stats.
(The extension itself is exercised in tests/test_extension.py; here the hub side is driven the way the extension drives it.)"""
import base64
import importlib
import io
import json
import shutil
import subprocess
import time

import pytest


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAUTO_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("AIAUTO_ARTIFACTS", str(tmp_path / "artifacts"))
    import aiauto.server.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        yield c, appmod, tmp_path / "artifacts"


def jpeg(color):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (800, 500), color).save(b, "JPEG")
    return base64.b64encode(b.getvalue()).decode()


STEPS = [
    {"action": "navigate", "value": "http://shop.test/", "raw": "open http://shop.test/", "source": "rules"},
    {"action": "type", "target": "Email", "value": "a@b.co", "raw": 'type "a@b.co" into Email', "source": "rules"},
    {"action": "type", "target": "Password", "value": "${password}", "raw": 'type "${password}" into Password', "source": "rules"},
    {"action": "click", "target": "Sign in button", "raw": "click Sign in button", "source": "rules"},
    {"action": "assert_text", "value": "Welcome", "raw": 'verify the page shows "Welcome"', "source": "rules"},
]
EL = {"tag": "input", "type": "text", "name": "Email", "label": "Email", "role": "textbox", "id": "email", "frames": [], "shadow": [],
      "locators": [{"strategy": "id", "value": "email"}, {"strategy": "label", "value": "Email"}]}


def start(c, status_steps=STEPS, name="Sign in"):
    r = c.post("/api/ext/runs", json={"name": name, "steps": status_steps, "data": {"password": "hunter2-secret"},
                                      "sensitive": ["password"], "url": "http://shop.test/"})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def play(c, rid, fail_at=None):
    ev = [{"type": "run_started"}]
    for i, s in enumerate(STEPS):
        ev.append({"type": "step_started", "index": i, "raw": s["raw"], "action": s["action"]})
        if i == fail_at:
            ev.append({"type": "step_finished", "index": i, "status": "failed", "error": "Expected page to contain 'Welcome'", "ms": 300,
                       "shot": "data:image/jpeg;base64," + jpeg("red"), "url": "http://shop.test/"})
            break
        ev.append({"type": "step_finished", "index": i, "status": "passed", "ms": 120 + i, "picked": "heuristic",
                   "shot": "data:image/jpeg;base64," + jpeg((40 * i, 120, 200)), "url": "http://shop.test/", "el": EL if i == 1 else None})
    assert c.post(f"/api/ext/runs/{rid}/events", json={"events": ev}).status_code == 200


def test_a_run_reported_by_the_extension_leaves_everything_an_app_run_leaves(hub):
    c, appmod, arts = hub
    with c.websocket_connect("/ws/ui") as ws:                            # someone watching live
        rid = start(c)
        play(c, rid)
        t0 = int(time.time() * 1000)
        frames = [{"t": t0 + 400 * i, "data": jpeg((20 * i, 90, 160))} for i in range(6)]
        assert c.post(f"/api/ext/runs/{rid}/frames", json={"frames": frames}).status_code == 200
        assert c.post(f"/api/ext/runs/{rid}/events", json={"events": [{"type": "run_finished", "status": "passed", "ended_ms": t0 + 3000}]}).status_code == 200
        seen = []
        end = time.time() + 5
        while time.time() < end and "run_finished" not in seen:
            seen.append(ws.receive_json()["type"])
    assert {"run_started", "step_started", "step_finished", "live_frame", "run_finished"} <= set(seen), seen

    run = c.get(f"/api/runs/{rid}").json()
    assert run["status"] == "passed" and run["engine"] == "extension" and run["agent"] == "Chrome extension"
    assert len(run["steps"]) == 5 and all(s["status"] == "passed" for s in run["steps"])
    assert run["video"] == "video.webm"
    d = arts / rid
    assert (d / "video.webm").stat().st_size > 1000
    ff = shutil.which("ffprobe") or shutil.which("ffmpeg")
    if shutil.which("ffprobe"):
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(d / "video.webm")],
                                   capture_output=True, text=True).stdout.strip())
        assert dur > 1.5
    assert len(list((d / "screenshots").glob("step-*.jpg"))) == 5
    html = (d / "report.html").read_text(encoding="utf-8")
    assert "Welcome" in html and "Sign in button" in html and "<video" in html
    for f in ("report.html", "summary.json", "script.json", "data.json", "test_playwright.py", "test_selenium.py"):
        assert "hunter2" not in (d / f).read_text(encoding="utf-8"), f            # the secret is in none of them
    script = json.loads((d / "script.json").read_text(encoding="utf-8"))
    assert script["steps"][1]["locator"]["strategy"] == "id" and script["steps"][1]["locator"]["value"] == "email"   # the element it acted on
    assert script["data"]["password"] != "hunter2-secret"


def test_stats_and_the_run_list_count_extension_runs_and_a_failure_is_reported_as_one(hub):
    c, appmod, arts = hub
    ok, bad = start(c, name="Good"), start(c, name="Bad")
    play(c, ok)
    play(c, bad, fail_at=4)
    c.post(f"/api/ext/runs/{ok}/events", json={"events": [{"type": "run_finished", "status": "passed"}]})
    c.post(f"/api/ext/runs/{bad}/events", json={"events": [{"type": "run_finished", "status": "failed"}]})
    runs = {r["run_id"]: r for r in c.get("/api/runs").json()}
    assert runs[ok]["status"] == "passed" and runs[bad]["status"] == "failed"
    stats = c.get("/api/stats").json()
    assert stats["total"] == 2 and stats["passed"] == 1 and stats["failed"] == 1
    b = c.get(f"/api/runs/{bad}").json()
    assert [s["status"] for s in b["steps"]] == ["passed", "passed", "passed", "passed", "failed"] and "Welcome" in b["steps"][4]["error"]
    assert not (arts / bad / "video.webm").exists()                           # no frames were sent: no video, and no crash


def test_a_past_run_and_a_library_script_come_back_for_replay_with_their_secrets_only_for_the_owner(hub):
    c, appmod, arts = hub
    rid = start(c)
    play(c, rid)
    c.post(f"/api/ext/runs/{rid}/events", json={"events": [{"type": "run_finished", "status": "passed"}]})
    r = c.get(f"/api/ext/replay/run/{rid}").json()
    assert r["test"]["data"]["password"] == "hunter2-secret" and r["needs"] == []         # the owner's replay has its secret again
    assert [s["action"] for s in r["test"]["steps"]][:2] == ["navigate", "type"]
    t = dict(r["test"])
    t["data"] = {**t["data"], "password": "hunter2-secret"}
    assert c.post("/api/library", json={"name": "Sign in flow", "test": t}).status_code == 200
    lib = c.get("/api/ext/replay/library/Sign-in-flow").json()
    assert lib["test"]["data"]["password"] == "" and lib["needs"] == ["password"]         # a saved script asks for it
    assert c.get("/api/ext/replay/run/nope").status_code == 404
