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
