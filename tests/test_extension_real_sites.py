"""Real websites through the extension: the same scenarios the server's real-site suite uses, run from the panel in a real Chromium (the
server's engine in the person's tab), then the generated Playwright and Selenium scripts run on their own, then the app replays the run.
Needs the internet; skipped when it is not there (AIAUTO_SKIP_REAL_SITES=1 skips it on purpose)."""
import os
import socket
import time
from pathlib import Path

import pytest

from .test_extension import close_extra, ready, sites  # noqa: F401
from .test_extension_server import (APP_CFG, _assert_scripts_run, _run_from_panel, api, browser, new_extension_run, rhub, runs, saved, wait_run)  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
RS = ROOT / "examples" / "real_sites"


def online(host="www.saucedemo.com"):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(bool(os.getenv("AIAUTO_SKIP_REAL_SITES")) or not online(), reason="no internet (or AIAUTO_SKIP_REAL_SITES)")

SCENARIOS = {
    "saucedemo checkout": (RS / "01_saucedemo_checkout.txt", "user = standard_user\npassword* = secret_sauce", {"AIAUTO_SECRET_PASSWORD": "secret_sauce"}),
    "the-internet: shadow DOM, nested frames, iframe editor, alerts": (RS / "02_internet_frames_shadow.txt", "", {}),
    "selenium.dev shadow root page": (RS / "06_shadow_root_checkbox.txt", "", {}),
    "todomvc (React)": (RS / "08_todomvc_react.txt", "", {}),
    "ui testing playground: dynamic ids, hidden / overlapping / ajax": (RS / "04_uitap_tricky_dom.txt", "pwd* = pwd", {"AIAUTO_SECRET_PWD": "pwd"}),
}


def first_url(text):
    for line in text.splitlines():
        if line.lower().startswith("open "):
            return line.split(None, 1)[1].strip()
    raise AssertionError("no Open step")


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_a_real_site_scenario_runs_in_the_tab_and_its_scripts_run_on_their_own(browser, rhub, tmp_path, name):
    path, data, secrets = SCENARIOS[name]
    text = path.read_text()
    base, arts = rhub
    for attempt in (1, 2):                    # a live site can have a bad moment: one retry of the run itself (never of the scripts)
        known = {r["run_id"] for r in runs(base)}
        status, panel, page = _run_from_panel(browser, first_url(text), text, data, timeout=420)
        steps = [(li.get_attribute("class").split()[-1], li.locator(".tx").inner_text(), li.locator(".err").inner_text() if li.locator(".err").count() else "")
                 for li in panel.locator("#steps li").all()]
        if status == "passed":
            break
    assert status == "passed", [s for s in steps if s[0] != "passed"][:3]
    saved(panel, timeout=120)
    rid = new_extension_run(base, known)
    row = wait_run(base, rid, timeout=240)
    assert row["status"] == "passed" and row["agent"] == "Chrome extension"
    time.sleep(1)
    (tmp_path / "scripts").mkdir()
    _assert_scripts_run(tmp_path / "scripts", arts / rid, secrets)                      # the scripts made from it, as plain scripts
    r = api(base, "/api/runs", {"replay_run_id": rid, "config": {**APP_CFG, "timeout_ms": 15000}, "agent": "local"})
    again = wait_run(base, r["run_ids"][0], timeout=420)                                 # and the app's replay of it
    assert again["status"] == "passed", [(s["raw"], s["error"]) for s in api(base, f"/api/runs/{again['run_id']}")["steps"] if s["error"]]


def test_amazon_as_far_as_this_network_lets_it(browser, rhub, tmp_path):
    """Amazon's home page, its 'Continue shopping' gate and the search box. (From some networks Amazon answers searches with 503 or a
    CAPTCHA: the full examples/amazon_cart.txt scenario is for a machine it likes.)"""
    if not online("www.amazon.in"):
        pytest.skip("amazon.in is not reachable from here")
    base, arts = rhub
    text = 'Open https://www.amazon.in\nClick Continue shopping if shown\nType "wireless mouse" into the search box\nVerify page contains "Sign in"'
    known = {r["run_id"] for r in runs(base)}
    status, panel, page = _run_from_panel(browser, "https://www.amazon.in", text, "", timeout=240)
    steps = [(li.get_attribute("class").split()[-1], li.locator(".tx").inner_text(), li.locator(".err").inner_text() if li.locator(".err").count() else "")
             for li in panel.locator("#steps li").all()]
    assert status == "passed", steps
    saved(panel, timeout=120)
    rid = new_extension_run(base, known)
    assert wait_run(base, rid)["status"] == "passed"
    (tmp_path / "scripts").mkdir()
    _assert_scripts_run(tmp_path / "scripts", arts / rid)
