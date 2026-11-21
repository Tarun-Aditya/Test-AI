"""Hub automation: cron schedules, CI hooks, failure notifications, flaky-test detection."""
import http.server
import importlib
import json
import threading
import time
from datetime import datetime

import pytest

from aiauto import automation as au


# ------------------------------------------------------------------------------------------ cron
def test_cron_fields_lists_ranges_steps_and_names():
    c = au.Cron("*/15 9-17 * * mon-fri")
    assert c.matches(datetime(2026, 11, 23, 9, 30))           # Monday
    assert not c.matches(datetime(2026, 11, 23, 9, 31))
    assert not c.matches(datetime(2026, 11, 22, 9, 30))       # Sunday
    assert au.Cron("0 6 1,15 * *").matches(datetime(2026, 12, 15, 6, 0))
    assert au.Cron("@daily").matches(datetime(2026, 1, 1, 0, 0))
    assert au.Cron("0 0 * * 7").matches(datetime(2026, 11, 22, 0, 0))      # 7 is Sunday too


def test_cron_next_and_errors():
    assert au.Cron("30 2 * * *").next_after(datetime(2026, 11, 21, 2, 30)) == datetime(2026, 11, 22, 2, 30)
    assert au.Cron("0 * * * *").next_after(datetime(2026, 11, 21, 8, 10)) == datetime(2026, 11, 21, 9, 0)
    for bad in ("", "* * * *", "61 * * * *", "* 25 * * *", "a b c d e", "*/0 * * * *"):
        with pytest.raises(au.CronError):
            au.Cron(bad)


# ------------------------------------------------------------------------------------------ flaky
def _runs(name, statuses, healed=0):
    """newest first, like DB.list_runs"""
    return [{"test_name": name, "status": s, "run_id": f"{name}-{i}", "healed": healed} for i, s in enumerate(reversed(statuses))]


def test_flaky_means_results_that_flip_not_a_test_that_broke():
    rows = (_runs("flip", ["passed", "failed", "passed", "passed", "failed", "passed"])
            + _runs("broken", ["passed", "passed", "passed", "failed", "failed", "failed"])
            + _runs("solid", ["passed"] * 6)
            + _runs("new", ["passed", "failed"]))
    rep = {r["name"]: r for r in au.flaky_report(rows)}
    assert rep["flip"]["flaky"] and rep["flip"]["flips"] == 4 and rep["flip"]["pass_rate"] == 0.667
    assert not rep["broken"]["flaky"] and rep["broken"]["last_status"] == "failed"      # failing, not flaky
    assert not rep["solid"]["flaky"] and rep["solid"]["pass_rate"] == 1.0
    assert not rep["new"]["flaky"]                                                       # too little history to say
    assert au.flaky_report(rows)[0]["name"] == "flip"                                    # flaky ones first


# ------------------------------------------------------------------------------------------ notifications
class Receiver:
    def __init__(self):
        self.got = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                outer.got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.srv.shutdown()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAUTO_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("AIAUTO_ARTIFACTS", str(tmp_path / "artifacts"))
    import aiauto.server.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        yield c, appmod


def test_notification_channels_get_the_right_message_for_the_right_runs(hub, receiver):
    c, appmod = hub
    slack = c.put("/api/notifications", json={"kind": "slack", "url": receiver.url + "/slack"}).json()
    hook = c.put("/api/notifications", json={"kind": "webhook", "url": receiver.url + "/hook", "on": "always"}).json()
    teams = c.put("/api/notifications", json={"kind": "teams", "url": receiver.url + "/teams", "on": "recovered"}).json()
    assert c.put("/api/notifications", json={"kind": "carrier-pigeon", "url": "x"}).status_code == 422
    assert c.put("/api/notifications", json={"kind": "slack", "url": "not-a-url"}).status_code == 422
    assert c.put("/api/notifications", json={"kind": "email"}).status_code == 422

    n = appmod.NOTIFIER
    failed = {"run_id": "r1", "test_name": "Checkout", "status": "failed", "passed": 3, "total": 5, "error": "Step 4 failed: no Pay button"}
    passed = {"run_id": "r2", "test_name": "Checkout", "status": "passed", "passed": 5, "total": 5}
    assert [x["ok"] for x in n.run_finished(failed)] == [True, True]            # slack (failed) + webhook (always)
    assert [x["ok"] for x in n.run_finished(passed)] == [True]                   # only the "always" webhook
    paths = [p for p, _ in receiver.got]
    assert paths.count("/slack") == 1 and paths.count("/hook") == 2 and "/teams" not in paths
    text = dict(receiver.got)["/slack"]["text"]
    assert "Checkout" in text and "failed" in text and "no Pay button" in text
    body = [b for p, b in receiver.got if p == "/hook"][0]
    assert body["status"] == "failed" and body["run_id"] == "r1" and body["steps"] == "3/5 steps passed"

    # the test button sends a clearly-marked message
    assert c.post(f"/api/notifications/{slack['id']}/test").status_code == 200
    assert "only a test" in receiver.got[-1][1]["text"]
    assert c.delete(f"/api/notifications/{hook['id']}").status_code == 200
    assert {r["id"] for r in c.get("/api/notifications").json()} == {slack["id"], teams["id"]}


def test_a_channel_that_is_down_never_breaks_the_run(hub):
    c, appmod = hub
    c.put("/api/notifications", json={"kind": "slack", "url": "http://127.0.0.1:9/nothing-listens"})
    out = appmod.NOTIFIER.run_finished({"run_id": "r", "test_name": "T", "status": "failed"})
    assert out and out[0]["ok"] is False and out[0]["error"]


# ------------------------------------------------------------------------------------------ schedules and hooks
SCRIPT = {"name": "Home page", "base_url": None, "data": {}, "sensitive": [],
          "steps": [{"action": "navigate", "value": "{{base}}", "raw": "Open the page"},
                    {"action": "assert_text", "value": "Shop", "raw": 'Verify the page shows "Shop"'}]}


def _save(c, shop_url):
    s = json.loads(json.dumps(SCRIPT).replace("{{base}}", shop_url))
    assert c.post("/api/library", json={"name": "home-page", "test": s}).status_code == 200


def _wait_done(c, ids, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        runs = {r["run_id"]: r for r in c.get("/api/runs").json()}
        if all(runs.get(i, {}).get("status") in ("passed", "failed", "interrupted") for i in ids):
            return runs
        time.sleep(0.5)
    raise AssertionError("runs did not finish")


CFG = {"engine": "playwright", "record_video": False, "use_llm": False, "timeout_ms": 15000}


def test_a_schedule_starts_its_script_once_in_its_minute(hub, shop_url):
    import asyncio
    c, appmod = hub
    _save(c, shop_url)
    assert c.put("/api/schedules", json={"library": "home-page", "cron": "nope"}).status_code == 422
    assert c.put("/api/schedules", json={"library": "missing", "cron": "* * * * *"}).status_code == 404
    s = c.put("/api/schedules", json={"name": "Nightly", "library": "home-page", "cron": "30 2 * * *", "config": CFG}).json()
    listed = c.get("/api/schedules").json()
    assert listed[0]["id"] == s["id"] and listed[0]["next_run"]
    prev = c.get("/api/schedules/preview", params={"cron": "*/20 * * * *", "count": 3}).json()
    assert len(prev["next"]) == 3

    off = datetime(2026, 11, 21, 3, 0).astimezone()
    assert asyncio.run(appmod.run_due_schedules(off)) == []                   # not its minute
    on = datetime(2026, 11, 21, 2, 30).astimezone()
    ids = asyncio.run(appmod.run_due_schedules(on))
    assert len(ids) == 1
    assert asyncio.run(appmod.run_due_schedules(on)) == []                    # same minute again (a restart): not twice
    assert _wait_done(c, ids)[ids[0]]["status"] == "passed"
    assert c.get("/api/schedules").json()[0]["last_run_ids"] == ids

    c.put("/api/schedules", json={"id": s["id"], "library": "home-page", "cron": "30 2 * * *", "enabled": False, "config": CFG})
    assert asyncio.run(appmod.run_due_schedules(datetime(2026, 11, 22, 2, 30).astimezone())) == []      # paused
    assert c.delete(f"/api/schedules/{s['id']}").status_code == 200


def test_a_ci_hook_runs_the_script_and_the_secret_is_shown_once(hub, shop_url):
    c, _ = hub
    _save(c, shop_url)
    h = c.post("/api/hooks", json={"name": "deploy", "library": "home-page", "config": CFG}).json()
    assert h["token"] and h["url"].endswith(h["token"])
    assert "token" not in c.get("/api/hooks").json()[0] and "token_hash" not in c.get("/api/hooks").json()[0]
    assert c.post("/api/hooks/run/wrong-token").status_code == 404
    r = c.post(h["url"])
    assert r.status_code == 200
    assert _wait_done(c, r.json()["run_ids"])[r.json()["run_ids"][0]]["status"] == "passed"
    assert c.delete(f"/api/hooks/{h['id']}").status_code == 200
    assert c.post(h["url"]).status_code == 404


def test_the_hook_url_needs_no_sign_in_but_nothing_else_does_not(tmp_path, monkeypatch, shop_url):
    """With accounts on, only the hook URL is open; managing hooks and notifications still needs a sign-in."""
    from aiauto.server import auth as authmod
    assert authmod.is_public("/api/hooks/run/abc")
    assert not authmod.is_public("/api/hooks")
    assert not authmod.is_public("/api/notifications")
    assert authmod.denied({"role": "tester"}, "PUT", "/api/notifications")            # channels are the admin's
    assert authmod.denied({"role": "admin"}, "PUT", "/api/notifications") is None


def test_flaky_endpoint_reads_the_run_history(hub, shop_url):
    c, _ = hub
    _save(c, shop_url)
    ids = [c.post("/api/runs", json={"library": "home-page", "config": CFG}).json()["run_ids"][0] for _ in range(2)]
    _wait_done(c, ids)
    rows = c.get("/api/flaky").json()
    assert rows and rows[0]["name"] == "Home page" and rows[0]["runs"] == 2 and not rows[0]["flaky"]
    assert c.get("/api/flaky", params={"only": True}).json() == []


# ------------------------------------------------------------------------------------------ time zone, history, try-before-save
def test_schedules_say_which_clock_they_use_and_remember_their_runs(hub, shop_url):
    import asyncio
    c, appmod = hub
    _save(c, shop_url)
    s = c.put("/api/schedules", json={"library": "home-page", "cron": "30 2 * * *", "config": CFG}).json()
    row = c.get("/api/schedules").json()[0]
    assert row["tz"]["label"] and row["tz"]["offset"][0] in "+-" and ":" in row["tz"]["offset"]
    assert c.get("/api/schedules/preview", params={"cron": "0 * * * *"}).json()["tz"] == row["tz"]
    ids = []
    for n in range(3):
        r = c.post(f"/api/schedules/{s['id']}/run").json()
        ids += r["run_ids"]
        _wait_done(c, r["run_ids"])
    hist = c.get("/api/schedules").json()[0]["history"]
    assert [h["ids"][0] for h in hist] == ids[::-1]                                  # newest first, each with its runs
    h = c.post("/api/hooks", json={"library": "home-page", "config": CFG}).json()
    run = c.post(h["url"]).json()["run_ids"]
    assert c.get("/api/hooks").json()[0]["history"][0]["ids"] == run


def test_a_channel_can_be_tried_before_it_is_saved(hub, receiver):
    c, _ = hub
    r = c.post("/api/notifications/test", json={"kind": "slack", "url": receiver.url + "/try"})
    assert r.status_code == 200 and "only a test" in receiver.got[-1][1]["text"]
    assert c.get("/api/notifications").json() == []                                   # nothing was saved
    assert c.post("/api/notifications/test", json={"kind": "slack", "url": ""}).status_code == 422
    assert c.post("/api/notifications/test", json={"kind": "slack", "url": "http://127.0.0.1:9/x"}).status_code == 502


# ------------------------------------------------------------------------------------------ baselines
def test_baselines_are_listed_shown_approved_and_deleted(hub, tmp_path):
    import http.server, functools
    c, appmod = hub
    page = tmp_path / "p.html"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/p.html"
        script = {"name": "Look ${x}", "base_url": None, "data": {}, "sensitive": [],
                  "steps": [{"action": "navigate", "value": url, "raw": "Open"}, {"action": "assert_visual", "value": "front", "raw": "Verify the page looks the same as front"}]}
        c.post("/api/library", json={"name": "look", "test": script})

        def go(color):
            page.write_text(f'<html><body style="margin:0"><div style="width:300px;height:200px;background:{color}"></div></body></html>')
            rid = c.post("/api/runs", json={"library": "look", "config": CFG}).json()["run_ids"][0]
            return rid, _wait_done(c, [rid])[rid]["status"]
        _, st = go("#3366cc")
        assert st == "passed"
        bl = c.get("/api/baselines").json()
        assert [(b["test"], b["name"]) for b in bl] == [("look", "front")]                  # ${x} dropped from the folder name, like the runner does
        img = c.get(bl[0]["url"])
        assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
        rid, st = go("#cc3333")
        assert st == "failed"
        assert c.post("/api/baselines/approve", json={"run_id": rid, "name": "nope"}).status_code == 404
        assert c.post("/api/baselines/approve", json={"run_id": rid, "name": "front"}).status_code == 200
        _, st = go("#cc3333")
        assert st == "passed"                                                               # the new look is the baseline now
        assert c.get("/api/baselines/%2e%2e/x.png").status_code in (400, 404)
        assert c.delete("/api/baselines/look/front").status_code == 200 and c.get("/api/baselines").json() == []
    finally:
        srv.shutdown()
