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


@pytest.fixture
def hub_and_agent(tmp_path):
    port = _port()
    env = {**os.environ, "AIAUTO_DB": str(tmp_path / "hub.db"), "AIAUTO_ARTIFACTS": str(tmp_path / "artifacts"),
           "AIAUTO_AGENT_TOKEN": "t0k", "ANTHROPIC_API_KEY": "",
           "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    procs = [subprocess.Popen([sys.executable, "-m", "aiauto", "server", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                httpx.get(base + "/api/health", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        # headed runs need a screen: give the agent a virtual one where Xvfb is installed
        wrap = ["xvfb-run", "-a"] if shutil.which("xvfb-run") else []
        procs.append(subprocess.Popen([*wrap, sys.executable, "-m", "aiauto", "agent", "--server", base, "--name", "box-2",
                                       "--token", "t0k", "--out", str(tmp_path / "agent_out")],
                                      cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(60):
            if httpx.get(base + "/api/health").json()["agents"] == 1:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("agent did not register")
        yield base, tmp_path, bool(wrap)
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_remote_agent_runs_streams_and_uploads(hub_and_agent, shop_url, engine):
    base, tmp, has_screen = hub_and_agent
    text = (EXAMPLES / "shop_natural.txt").read_text()
    data = {"base_url": shop_url, "product": "wireless mouse", "brand": "Logitech", "qty": "2"}
    modes = [True, False] if has_screen else [True]          # headless, and headed on the virtual screen
    frames, runs = {}, {}
    with connect(base.replace("http", "ws") + "/ws/ui", max_size=None) as ws:
        for headless in modes:
            r = httpx.post(base + "/api/runs", timeout=30, json={
                "text": text, "data": data, "sensitive": ["qty"], "agent": "box-2",
                "config": {"engine": engine, "headless": headless, "use_llm": False, "live_view": True}})
            assert r.status_code == 200, r.text
            runs[r.json()["run_ids"][0]] = headless
        done, end = set(), time.time() + 240
        while len(done) < len(runs) and time.time() < end:
            try:
                m = json.loads(ws.recv(timeout=5))
            except TimeoutError:
                continue
            rid = m.get("run_id")
            if m.get("type") == "live_frame" and rid in runs:
                frames[rid] = frames.get(rid, 0) + 1
            if m.get("type") == "run_finished" and rid in runs:
                done.add(rid)
    assert done == set(runs), "runs did not finish"
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
