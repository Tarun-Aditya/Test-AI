"""A real remote agent: hub and agent run as separate processes, the agent connects over WebSocket, runs the job,
streams live frames to UI subscribers and uploads its artifacts (report, video, scripts) to the hub."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest
from websockets.sync.client import connect

from .conftest import EXAMPLES

ROOT = EXAMPLES.parent


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_hub(tmp_path, procs, env):
    port = _port()
    procs.append(subprocess.Popen([sys.executable, "-m", "aiauto", "server", "--host", "127.0.0.1", "--port", str(port)],
                                  cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            httpx.get(base + "/api/health", timeout=1)
            return base
        except httpx.HTTPError:
            time.sleep(0.25)
    raise AssertionError("hub did not start")


def _start_agent(base, tmp_path, procs, env, name, slots=1):
    # headed runs need a screen: give the agent a virtual one where Xvfb is installed
    wrap = ["xvfb-run", "-a"] if shutil.which("xvfb-run") else []
    procs.append(subprocess.Popen([*wrap, sys.executable, "-m", "aiauto", "agent", "--server", base, "--name", name,
                                   "--token", "t0k", "--slots", str(slots), "--out", str(tmp_path / f"out_{name}")],
                                  cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return bool(wrap)


def _wait_agents(base, n):
    for _ in range(80):
        if httpx.get(base + "/api/health").json()["agents"] == n:
            return
        time.sleep(0.25)
    raise AssertionError("agents did not register")


@pytest.fixture
def cluster(tmp_path):
    """start(agents={"name": slots, ...}) -> (hub url, has_screen). Everything is stopped afterwards."""
    procs = []
    env = {**os.environ, "AIAUTO_DB": str(tmp_path / "hub.db"), "AIAUTO_ARTIFACTS": str(tmp_path / "artifacts"),
           "AIAUTO_AGENT_TOKEN": "t0k", "ANTHROPIC_API_KEY": "", "AIAUTO_LOCAL_WORKERS": "1",
           "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}

    def start(agents):
        base = _start_hub(tmp_path, procs, env)
        screen = False
        for name, slots in agents.items():
            screen = _start_agent(base, tmp_path, procs, env, name, slots)
        _wait_agents(base, len(agents))
        return base, screen
    try:
        yield start
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.fixture
def hub_and_agent(cluster, tmp_path):
    base, screen = cluster({"box-2": 1})
    return base, tmp_path, screen


def _collect(base, submit, timeout=300):
    """Subscribe to /ws/ui, then submit() -> run ids; follow until every run finished.
    Returns (run ids, live frame counts, start times, end times)."""
    frames, started, ended = {}, {}, {}
    with connect(base.replace("http", "ws") + "/ws/ui", max_size=None) as ws:
        run_ids = submit()
        end = time.time() + timeout
        while len(ended) < len(run_ids) and time.time() < end:
            listed = {x["run_id"]: x for x in httpx.get(base + "/api/runs").json()}
            for rid in run_ids:        # runs that finished before we subscribed
                if rid not in ended and listed.get(rid, {}).get("status") in ("passed", "failed", "interrupted"):
                    ended[rid] = time.time()
            try:
                m = json.loads(ws.recv(timeout=2))
            except TimeoutError:
                continue
            rid = m.get("run_id")
            if rid not in run_ids:
                continue
            if m.get("type") == "live_frame":
                frames[rid] = frames.get(rid, 0) + 1
            elif m.get("type") == "run_started":
                started[rid] = time.time()
            elif m.get("type") == "run_finished":
                ended[rid] = time.time()
    assert set(ended) == set(run_ids), "runs did not finish"
    return run_ids, frames, started, ended


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_remote_agent_runs_streams_and_uploads(hub_and_agent, shop_url, engine):
    base, tmp, has_screen = hub_and_agent
    text = (EXAMPLES / "shop_natural.txt").read_text()
    data = {"base_url": shop_url, "product": "wireless mouse", "brand": "Logitech", "qty": "2"}
    modes = [True, False] if has_screen else [True]          # headless, and headed on the virtual screen
    runs = {}

    def submit():
        for headless in modes:
            r = httpx.post(base + "/api/runs", timeout=30, json={
                "text": text, "data": data, "sensitive": ["qty"], "agent": "box-2",
                "config": {"engine": engine, "headless": headless, "use_llm": False, "live_view": True}})
            assert r.status_code == 200, r.text
            runs[r.json()["run_ids"][0]] = headless
        return list(runs)
    _, frames, _, _ = _collect(base, submit)
    for _ in range(40):                                       # artifacts upload right after run_finished
        listed = {x["run_id"]: x for x in httpx.get(base + "/api/runs").json()}
        if all(listed[r].get("video") for r in runs):
            break
        time.sleep(0.5)
    for rid, headless in runs.items():
        run = listed[rid]
        assert run["status"] == "passed" and run["agent"] == "box-2", (headless, run.get("error"))
        assert frames.get(rid, 0) >= 2, f"no live view ({'headless' if headless else 'headed'})"
        art = tmp / "artifacts" / rid
        summary = json.loads((art / "summary.json").read_text())    # valid JSON despite the 1-character secret
        assert summary["agent"] == "box-2"
        assert run["video"] and (art / run["video"]).stat().st_size > 1000
        assert {"report.html", "test_playwright.py", "test_selenium.py", "autopilot_runtime.py"} <= {p.name for p in art.iterdir()}


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def test_parallel_runs_on_several_machines_and_both_engines(cluster, shop_url):
    """Point one submit at two machines: every run on EACH machine, with Playwright AND Selenium, at the same time.
    box-a has 2 slots (two browsers at once), box-b 1; the hub never exceeds a machine's slots."""
    base, _ = cluster({"box-a": 2, "box-b": 1})
    text = (EXAMPLES / "shop_natural.txt").read_text()
    data = {"base_url": shop_url, "product": "wireless mouse", "brand": "Logitech", "qty": "2"}
    machines = {m["name"]: m for m in httpx.get(base + "/api/agents").json()}
    assert machines["box-a"]["slots"] == 2 and machines["box-b"]["slots"] == 1

    def submit():
        r = httpx.post(base + "/api/runs", timeout=30, json={
            "text": text, "data": data, "agent": ["box-a", "box-b"], "spread": "each", "engines": ["playwright", "selenium"],
            "config": {"headless": True, "use_llm": False, "record_video": False, "live_view": True}})
        assert r.status_code == 200, r.text
        assert len(r.json()["run_ids"]) == 4 and r.json()["batch_id"]
        return r.json()["run_ids"]
    ids, frames, started, ended = _collect(base, submit)
    runs = {x["run_id"]: x for x in httpx.get(base + "/api/runs").json()}
    assert all(runs[i]["status"] == "passed" for i in ids), [(runs[i]["test_name"], runs[i].get("error")) for i in ids]
    assert sorted((runs[i]["agent"], runs[i]["engine"]) for i in ids) == [
        ("box-a", "playwright"), ("box-a", "selenium"), ("box-b", "playwright"), ("box-b", "selenium")]
    assert all(" · " in runs[i]["test_name"] for i in ids)          # labels say which engine / machine
    span = {i: (started[i], ended[i]) for i in ids}
    on = lambda m: [i for i in ids if runs[i]["agent"] == m]
    a1, a2 = on("box-a")
    assert _overlap(span[a1], span[a2]), "box-a (2 slots) should run both of its tests at the same time"
    b1, b2 = on("box-b")
    assert not _overlap(span[b1], span[b2]), "box-b has 1 slot: its tests must run one after the other"
    assert any(_overlap(span[a], span[b]) for a in on("box-a") for b in on("box-b")), "machines should work in parallel"


def test_pool_spreads_iterations_over_free_machines(cluster, shop_url):
    """"pool": iterations go to whichever of the chosen machines has a free slot."""
    base, _ = cluster({"box-a": 1, "box-b": 1})
    text = (EXAMPLES / "shop_natural.txt").read_text()
    its = [{"data": {"brand": "Logitech", "qty": str(q)}} for q in (1, 2, 3, 1)]
    def submit():
        r = httpx.post(base + "/api/runs", timeout=30, json={
            "text": text, "data": {"base_url": shop_url, "product": "wireless mouse"}, "iterations": its,
            "agent": ["box-a", "box-b"], "spread": "pool", "config": {"engine": "selenium", "headless": True, "use_llm": False, "record_video": False}})
        assert r.status_code == 200, r.text
        return r.json()["run_ids"]
    ids, *_ = _collect(base, submit)
    runs = {x["run_id"]: x for x in httpx.get(base + "/api/runs").json()}
    assert all(runs[i]["status"] == "passed" for i in ids), [runs[i].get("error") for i in ids]
    per = {m: sum(runs[i]["agent"] == m for i in ids) for m in ("box-a", "box-b")}
    assert per["box-a"] >= 1 and per["box-b"] >= 1 and sum(per.values()) == 4, per
