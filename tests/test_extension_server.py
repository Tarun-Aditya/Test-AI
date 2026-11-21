"""The extension is another way to run the server's own engine: its panel carries the person's tab to the hub, whose runner and
Playwright engine run the scenario in that tab. Everything is checked here in a real Chromium with the extension loaded next to a
real hub: the same scenario the server's own suite runs (every kind of input, upload, dialogs, drag, nested shadow roots and
iframes, secrets), what each run leaves on the hub (live events, screenshots, a video of the tab, report, stats, script), replays of
a run and of a recording by the app and by the extension, and failures.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from .test_extension import (EXT, ROOT, _record_order_flow, _start_hub, close_extra, ready, run_scenario, sites)  # noqa: F401

ORDER = ("The visitor enters the name Asha, selects 2 as the quantity and clicks Place order. The page should show Thanks, Asha.")
APP_CFG = {"engine": "playwright", "headless": True, "record_video": False, "use_llm": False, "timeout_ms": 20000}


@pytest.fixture(scope="module")
def rhub(tmp_path_factory):
    d = tmp_path_factory.mktemp("rhub")
    p, base = _start_hub(d)
    yield base, d / "art"
    p.terminate()


@pytest.fixture(scope="module")
def browser(tmp_path_factory, rhub):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(tmp_path_factory.mktemp("rprofile")), headless=False, executable_path=os.getenv("AIAUTO_CHROME_BINARY") or None,
        viewport={"width": 1100, "height": 800},
        args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--no-sandbox"])
    try:
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    except Exception:
        ctx.close()
        pw.stop()
        pytest.skip("this Chromium did not load the unpacked extension")
    ext = sw.url.split("/")[2]
    sw.evaluate("(hub) => chrome.storage.local.set({hub})", rhub[0])
    ctx.on("dialog", lambda d: None)
    ctx.sw = sw
    yield ctx, ext
    ctx.close()
    pw.stop()


def api(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Requested-With": "autopilot"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def runs(base):
    return api(base, "/api/runs")


def wait_run(base, run_id, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        r = next((x for x in runs(base) if x["run_id"] == run_id), None)
        if r and r["status"] in ("passed", "failed", "interrupted", "cancelled"):
            return r
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish")


def new_extension_run(base, before):
    end = time.time() + 30
    while time.time() < end:
        fresh = [r for r in runs(base) if r["agent"] == "Chrome extension" and r["run_id"] not in before]
        if fresh:
            return fresh[0]["run_id"]
        time.sleep(0.3)
    raise AssertionError("the run never appeared on the hub")


def saved(panel, timeout=60):
    """The panel says the report is on the hub; returns its text."""
    end = time.time() + timeout
    while time.time() < end:
        t = panel.locator("#reportline").inner_text() if panel.locator("#reportline").is_visible() else ""
        if t.startswith("Saved on the hub"):
            return t
        time.sleep(0.3)
    raise AssertionError("the report was never saved: " + (panel.locator("#reportline").inner_text() if panel.locator("#reportline").is_visible() else "no report line"))


class Watcher(threading.Thread):
    """Someone with the hub's live view open (the same /ws/ui the web app uses)."""
    def __init__(self, base):
        super().__init__(daemon=True)
        self.url = base.replace("http", "ws") + "/ws/ui"
        self.events, self.stop = [], False

    def run(self):
        from websockets.sync.client import connect
        with connect(self.url, additional_headers={"Origin": self.url.replace("ws", "http").rsplit("/ws", 1)[0]}) as ws:
            while not self.stop:
                try:
                    self.events.append(json.loads(ws.recv(timeout=0.5)))
                except TimeoutError:
                    pass
                except Exception:
                    return


def duration(path):
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    return float(subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip() or 0)


def standalone(tmp_path, run_dir, script, env_extra=None):
    """Copy a generated script and its runtime elsewhere and run it as a plain script: independent of this tool."""
    alone = tmp_path / f"alone_{script[:-3]}"
    alone.mkdir()
    for f in (script, "autopilot_runtime.py"):
        (alone / f).write_text((run_dir / f).read_text())
    out = tmp_path / f"res_{script[:-3]}"
    env = {**os.environ, "HEADLESS": "1", "NO_VIDEO": "1", "RESULTS_DIR": str(out), **(env_extra or {})}
    r = subprocess.run([sys.executable, "-I", str(alone / script)], cwd=alone, env=env, capture_output=True, text=True, timeout=300)
    return r, out



# ---------------------------------------------------------------------------------------------- run -> hub
@pytest.fixture(scope="module")
def first_run(browser, sites, rhub):
    """One scenario run from the panel with someone watching live. Shared by the tests that look at what it left."""
    base, arts = rhub
    before = {r["run_id"] for r in runs(base)}
    watch = Watcher(base)
    watch.start()
    time.sleep(0.5)
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/extension/order.html", ORDER)
    assert status == "passed", steps
    line = saved(panel)
    rid = new_extension_run(base, before)
    row = wait_run(base, rid)
    end = time.time() + 20
    while time.time() < end and not (arts / rid / "report.html").exists():
        time.sleep(0.3)
    time.sleep(1)
    watch.stop = True
    yield {"id": rid, "row": row, "arts": arts / rid, "events": watch.events, "line": line, "site": sites["site"], "page": page, "panel": panel}


def test_a_run_from_the_panel_is_a_run_on_the_hub_with_its_steps_screenshots_video_and_report(first_run, rhub):
    base, _ = rhub
    r, d = first_run["row"], first_run["arts"]
    assert r["status"] == "passed" and r["engine"] == "playwright" and r["agent"] == "Chrome extension" and r["browser"] == "chrome"
    detail = api(base, f"/api/runs/{first_run['id']}")
    assert [s["status"] for s in detail["steps"]] == ["passed"] * 5 and detail["video"] == "video.webm"
    assert any(s["picked_by"] == "heuristic" for s in detail["steps"])            # ranked by the server's own element finder
    assert len([f for f in (d / "screenshots").iterdir() if f.is_file()]) >= 4
    assert (d / "video.webm").stat().st_size > 2000
    dur = duration(d / "video.webm")
    assert dur is None or dur >= 1.0, dur
    exe = shutil.which("ffmpeg")
    if exe:                                          # the video shows the page: a frame from the end of it is not blank
        png = d / "_frame.png"
        subprocess.run([exe, "-y", "-loglevel", "error", "-sseof", "-0.3", "-i", str(d / "video.webm"), "-frames:v", "1", str(png)], check=True)
        from PIL import Image, ImageStat
        im = Image.open(png).convert("L")
        assert im.size == (1280, 720) and ImageStat.Stat(im).stddev[0] > 8, "the video's last frame is blank"
        im.save(ROOT / "dist" / "video-last-frame.png") if (ROOT / "dist").exists() else None
        png.unlink()
    html = (d / "report.html").read_text(encoding="utf-8")
    assert "Thanks, Asha" in html and "<video" in html and "Place order" in html
    assert "Saved on the hub" in first_run["line"]
    for f in ("test_playwright.py", "test_selenium.py", "script.json", "summary.json"):
        assert (d / f).exists(), f


def test_someone_watching_the_hub_sees_the_run_live(first_run):
    kinds = [e["type"] for e in first_run["events"] if e.get("run_id") in (None, first_run["id"])]
    for k in ("run_started", "step_started", "step_finished", "live_frame", "run_finished"):
        assert k in kinds, (k, sorted(set(kinds)))
    assert kinds.index("run_started") < kinds.index("step_finished") < kinds.index("run_finished")
    finished = [e for e in first_run["events"] if e.get("type") == "step_finished" and e.get("run_id") == first_run["id"]]
    assert [e["status"] for e in finished] == ["passed"] * 5
    frames = [e for e in first_run["events"] if e.get("type") == "live_frame"]
    assert frames and frames[0]["mime"] == "image/jpeg" and len(frames[0]["image"]) > 1000


def test_the_dashboard_stats_and_the_run_list_count_it(first_run, rhub):
    base, _ = rhub
    s = api(base, "/api/stats")
    assert s["total"] >= 1 and s["passed"] >= 1
    assert any(r["run_id"] == first_run["id"] and r["agent"] == "Chrome extension" for r in runs(base))


def test_the_hubs_own_pages_show_the_extension_run_with_its_video(browser, first_run, rhub):
    ctx, _ = browser
    page = ctx.new_page()
    page.goto(f"{rhub[0]}/#/runs/{first_run['id']}")
    page.wait_for_selector("text=Chrome extension", timeout=20000)
    body = page.locator("body").inner_text()
    assert "Place order" in body and "All passed" in body
    page.get_by_role("tab", name="Video").click() if page.get_by_role("tab", name="Video").count() else page.locator("text=Video").first.click()
    page.wait_for_selector("video", timeout=20000)
    (ROOT / "dist").mkdir(exist_ok=True)
    page.screenshot(path=str(ROOT / "dist" / "hub-run-from-extension.png"))
    page.close()


def test_the_run_replays_in_the_app_with_playwright(first_run, rhub):
    base, _ = rhub
    r = api(base, "/api/runs", {"replay_run_id": first_run["id"], "config": APP_CFG, "agent": "local"})
    row = wait_run(base, r["run_ids"][0])
    detail = api(base, f"/api/runs/{row['run_id']}")
    assert row["status"] == "passed", [(s["raw"], s["error"]) for s in detail["steps"]]
    assert row["mode"] == "replay" and row["source_run"] == first_run["id"]
    assert detail["steps"][0]["raw"].startswith("open ")                     # it opens the page the run started on
    script = json.loads((first_run["arts"] / "script.json").read_text(encoding="utf-8"))
    assert any(s.get("locator") for s in script["steps"])                    # exact elements, not a new guess


def test_the_run_replays_in_the_extension_from_the_hubs_recent_runs(browser, first_run, rhub):
    base, _ = rhub
    ctx, ext = browser
    close_extra(browser)
    before = {r["run_id"] for r in runs(base)}
    page = ctx.new_page()
    page.goto(first_run["site"] + "/extension/order.html")
    page.bring_to_front()
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#hubd summary").click()
    panel.locator(f'#hublists button[data-id="{first_run["id"]}"]').click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=60000)
    assert panel.locator("#banner").get_attribute("class").split()[-1] == "passed", panel.locator("#steps").inner_text()
    assert "element: script" in panel.locator("#steps").inner_text()         # found by the locators the first run stored
    saved(panel)
    rid = new_extension_run(base, before)
    row = wait_run(base, rid)
    assert row["status"] == "passed" and row["mode"] == "replay" and row["source_run"] == first_run["id"]
    assert "Thanks, Asha" in page.locator("#out").inner_text()


# ---------------------------------------------------------------------------------------------- recorder -> hub
def test_a_recording_saved_to_the_hub_replays_in_the_app_and_in_the_extension(browser, sites, rhub):
    base, _ = rhub
    ctx, ext = browser
    panel, page = _record_order_flow(browser, sites)
    panel.locator("#recname").fill("Order flow")
    panel.locator("#recsave").click()
    end = time.time() + 15
    while time.time() < end and not any(x["name"] == "Order-flow" for x in api(base, "/api/library")):
        time.sleep(0.3)
    assert any(x["name"] == "Order-flow" and x["steps"] >= 5 for x in api(base, "/api/library"))
    # the app, with Playwright
    r = api(base, "/api/runs", {"library": "Order-flow", "config": APP_CFG, "agent": "local"})
    row = wait_run(base, r["run_ids"][0])
    detail = api(base, f"/api/runs/{row['run_id']}")
    assert row["status"] == "passed", [(s["raw"], s["error"]) for s in detail["steps"]]
    # the extension, from the hub's list
    before = {x["run_id"] for x in runs(base)}
    page.goto(page.url)
    page.bring_to_front()
    panel.locator("#mrun").click()
    panel.locator("#hubd summary").click()
    panel.locator('#hublists button[data-id="Order-flow"]').click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=60000)
    assert panel.locator("#banner").get_attribute("class").split()[-1] == "passed", panel.locator("#steps").inner_text()
    saved(panel)
    rid = new_extension_run(base, before)
    assert wait_run(base, rid)["status"] == "passed"
    assert "Thanks, Asha" in page.locator("#out").inner_text()


# ---------------------------------------------------------------------------------------------- secrets, failures, switches
def test_a_password_never_reaches_the_hubs_files_and_the_replay_in_the_app_still_has_it(browser, sites, rhub):
    base, arts = rhub
    before = {r["run_id"] for r in runs(base)}
    status, steps, panel, page = run_scenario(
        browser, sites["site"] + "/signup/signup.html",
        "The visitor lands on the sign up page and enters a valid email address test.user@example.com, a username testuser, age 30 and a "
        "password of at least 8 characters, then submits the registration form. The system should confirm that the account was created successfully.")
    assert status == "passed", steps
    saved(panel)
    rid = new_extension_run(base, before)
    wait_run(base, rid)
    time.sleep(1)
    secret = None
    for f in (arts / rid).rglob("*"):
        if f.is_file() and f.suffix in (".html", ".json", ".py", ".txt"):
            t = f.read_text(encoding="utf-8", errors="ignore")
            secret = secret or next((w for w in ("Passw0rd!", "Test@1234", "password123") if w in t), None)
    assert secret is None
    body = json.dumps(api(base, f"/api/runs/{rid}"))
    assert "••••" in body or "password" in body
    r = api(base, "/api/runs", {"replay_run_id": rid, "config": APP_CFG, "agent": "local"})
    row = wait_run(base, r["run_ids"][0])
    assert row["status"] == "passed", [(s["raw"], s["error"]) for s in api(base, f"/api/runs/{row['run_id']}")["steps"]]


def test_a_failing_scenario_is_reported_as_a_failure_with_its_evidence(browser, sites, rhub):
    base, arts = rhub
    before = {r["run_id"] for r in runs(base)}
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/extension/order.html",
                                              "The visitor enters the name Asha and clicks Place order. The page should show Order shipped.")
    assert status == "failed"
    saved(panel)
    rid = new_extension_run(base, before)
    row = wait_run(base, rid)
    assert row["status"] == "failed"
    detail = api(base, f"/api/runs/{rid}")
    bad = [s for s in detail["steps"] if s["status"] == "failed"]
    assert bad and "Order shipped" in bad[0]["error"]
    assert (arts / rid / "video.webm").exists() and any((arts / rid / "screenshots").iterdir())
    assert "Order shipped" in (arts / rid / "report.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------- the server's own scenarios, from the panel
def _run_from_panel(browser, url, text, data="", timeout=240):
    ctx, ext = browser
    close_extra(browser)
    page = ctx.new_page()
    page.goto(url)
    page.bring_to_front()
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#text").fill(text)
    if data:
        panel.locator("#datad summary").click()
        panel.locator("#data").fill(data)
    panel.locator("#run").click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=timeout * 1000)
    status = panel.locator("#banner").get_attribute("class").split()[-1]
    return status, panel, page


def test_the_servers_playground_scenario_runs_in_the_tab_exactly_as_in_the_app(browser, sites, rhub, tmp_path):
    """Every kind of input (text, date, range, colour, select, multi-select, radio, checkbox), a file upload, double and right click,
    native dialogs, a contenteditable editor, a hover menu, drag and drop, lazy loading, 'the second ...', nested shadow roots and
    nested iframes, secrets: the same scenario file the server's own suite runs, run from the extension's panel."""
    from pathlib import Path
    base, arts = rhub
    resume = tmp_path / "resume.txt"
    resume.write_text("cv")
    url = sites["site"] + "/playground.html"
    text = (Path(ROOT / "examples" / "playground_test.txt").read_text().replace("PLAYGROUND_URL", url).replace("UPLOAD_FILE", str(resume)))
    data = "\n".join(["first = Asha", "last = Rao", "email = asha@example.com", "password* = S3cret!!", "card* = 4111111111114242", "country = India"])
    before = {r["run_id"] for r in runs(base)}
    status, panel, page = _run_from_panel(browser, url, text, data)
    steps = [(li.get_attribute("class").split()[-1], li.locator(".tx").inner_text(), li.locator(".err").inner_text() if li.locator(".err").count() else "")
             for li in panel.locator("#steps li").all()]
    assert status == "passed", [s for s in steps if s[0] != "passed"][:3]
    saved(panel)
    rid = new_extension_run(base, before)
    row = wait_run(base, rid)
    assert row["status"] == "passed" and row["engine"] == "playwright" and row["agent"] == "Chrome extension"
    detail = api(base, f"/api/runs/{rid}")
    assert len(detail["steps"]) >= 40 and all(s["status"] == "passed" for s in detail["steps"])
    d = arts / rid
    script = json.loads((d / "script.json").read_text(encoding="utf-8"))
    labels = []
    for st in script["steps"]:
        for loc in [st.get("locator")] if st.get("locator") else []:
            labels.append(("iframe:" * len(loc.get("frames", []))) + ("shadow:" * len(loc.get("shadow", []))) + loc["strategy"])
    assert any("shadow:shadow:" in l for l in labels), labels                      # nested shadow roots
    assert any(l.startswith("iframe:iframe:") for l in labels), labels             # nested iframes
    assert any(st.get("locator") and (st["locator"]["strategy"] == "role" or "get_by_role" in (st["locator"].get("code") or "")) for st in script["steps"])   # semantic native locators
    for f in d.rglob("*"):                                                         # secrets never land in the hub's files
        if f.is_file() and f.suffix in (".json", ".py", ".html", ".txt"):
            body = f.read_text(errors="ignore")
            assert "S3cret!!" not in body and "4111111111114242" not in body, f
    assert (d / "video.webm").stat().st_size > 10_000
    from aiauto.scriptgen import extract_spec
    for name in ("test_playwright.py", "test_selenium.py"):                        # the generated scripts carry the spec and compile
        src = (d / name).read_text()
        compile(src, name, "exec")
        assert len(extract_spec(src).steps) == len(script["steps"])
    # the scripts made from what the extension ran run on their own: copied elsewhere, no tool on their path
    for script in ("test_playwright.py", "test_selenium.py"):
        res, out = standalone(tmp_path, d, script, {"AIAUTO_SECRET_PASSWORD": "S3cret!!", "AIAUTO_SECRET_CARD": "4111111111114242"})
        assert res.returncode == 0 and "PASSED" in res.stdout, (script, (res.stdout + res.stderr)[-2500:])
    # the app replays what the extension ran
    r = api(base, "/api/runs", {"replay_run_id": rid, "config": {**APP_CFG, "timeout_ms": 8000}, "agent": "local"})
    again = wait_run(base, r["run_ids"][0], timeout=240)
    assert again["status"] == "passed", [(s["raw"], s["error"]) for s in api(base, f"/api/runs/{again['run_id']}")["steps"] if s["error"]]


def test_a_virtualised_list_and_a_lazy_section_work_in_the_tab(browser, sites, rhub):
    base, _ = rhub
    before = {r["run_id"] for r in runs(base)}
    url = sites["site"] + "/virtual.html"
    status, panel, page = _run_from_panel(browser, url, 'Click Open order 750\nVerify page contains "Opened order 750"\nClick Subscribe to newsletter\nVerify page contains "Subscribed"')
    assert status == "passed", panel.locator("#steps").inner_text()
    saved(panel)
    rid = new_extension_run(base, before)
    assert wait_run(base, rid)["status"] == "passed"
    r = api(base, "/api/runs", {"replay_run_id": rid, "config": {**APP_CFG, "timeout_ms": 8000}, "agent": "local"})   # the replay re-finds row 750
    assert wait_run(base, r["run_ids"][0], timeout=120)["status"] == "passed"


def test_a_page_that_changed_its_ids_still_replays_through_the_servers_fallback_locators(browser, sites, rhub):
    base, _ = rhub
    ctx, ext = browser
    known = {r["run_id"] for r in runs(base)}
    status, panel, page = _run_from_panel(browser, sites["site"] + "/extension/order.html", ORDER)
    assert status == "passed"
    saved(panel)
    rid = new_extension_run(base, known)
    wait_run(base, rid)
    before = {r["run_id"] for r in runs(base)}
    page.goto(sites["site"] + "/extension/order2.html")                           # same labels and texts, every id changed
    page.bring_to_front()
    panel.locator("#hubd summary").click()
    panel.locator(f'#hublists button[data-id="{rid}"]').click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=90000)
    assert panel.locator("#banner").get_attribute("class").split()[-1] == "passed", panel.locator("#steps").inner_text()
    new = new_extension_run(base, before)
    row = wait_run(base, new)
    assert row["status"] == "passed", row
    steps = api(base, f"/api/runs/{new}")["steps"]
    assert all(st["status"] == "passed" for st in steps) and any(st["picked_by"] in ("script", "fallback", "heal") or st["healed"] for st in steps), steps   # the stored locators / fallbacks, as in the app


# ---------------------------------------------------------------------------------------------- the scripts of REPLAYS work as they are
class _Page(threading.Thread):
    """A page whose content can change between runs at the same address (a site that changed after the script was made)."""
    def __init__(self, html):
        super().__init__(daemon=True)
        import http.server as hs
        me = self

        class H(hs.BaseHTTPRequestHandler):
            def do_GET(self):
                body = me.html.encode()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.html = html
        self.srv = hs.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/order"

    def run(self):
        self.srv.serve_forever()


def _assert_scripts_run(tmp_path, run_dir, secrets=None):
    for script in ("test_playwright.py", "test_selenium.py"):
        res, out = standalone(tmp_path, run_dir, script, secrets)
        assert res.returncode == 0 and "PASSED" in res.stdout, (script, (res.stdout + res.stderr)[-2500:])


def test_the_scripts_of_replays_work_as_they_are_even_after_the_page_changed(browser, sites, rhub, tmp_path):
    """Run in natural language -> script. Replay it (replay mode) -> the replay's own script. Change the page, replay again: the engine
    heals, and the script of THAT replay carries the healed locators and runs on its own on the changed page."""
    base, arts = rhub
    original = (ROOT / "examples" / "extension" / "order.html").read_text()
    page_srv = _Page(original)
    page_srv.start()
    known = {r["run_id"] for r in runs(base)}
    status, panel, page = _run_from_panel(browser, page_srv.url, ORDER)
    assert status == "passed"
    saved(panel)
    first = new_extension_run(base, known)
    assert wait_run(base, first)["status"] == "passed"
    time.sleep(1)
    (tmp_path / "first").mkdir()
    _assert_scripts_run(tmp_path / "first", arts / first)

    # replay mode, same page: the replay's script works as it is
    r = api(base, "/api/runs", {"replay_run_id": first, "config": APP_CFG, "agent": "local"})
    again = wait_run(base, r["run_ids"][0])
    assert again["status"] == "passed" and again["mode"] == "replay"
    (tmp_path / "again").mkdir()
    _assert_scripts_run(tmp_path / "again", arts / again["run_id"])

    # the site changes: the name field is renamed and re-id'd, the quantity gets a new id
    page_srv.html = (original.replace('id="name"', 'id="visitor-full-name"').replace('for="name"', 'for="visitor-full-name"')
                     .replace(">Name<", ">Your full name<").replace('getElementById(\'name\')', "getElementById('visitor-full-name')")
                     .replace('id="qty"', 'id="ticket-count"').replace('for="qty"', 'for="ticket-count"').replace("getElementById('qty')", "getElementById('ticket-count')"))
    r = api(base, "/api/runs", {"replay_run_id": first, "config": APP_CFG, "agent": "local"})
    healed = wait_run(base, r["run_ids"][0])
    detail = api(base, f"/api/runs/{healed['run_id']}")
    assert healed["status"] == "passed", [(s["raw"], s["error"]) for s in detail["steps"]]
    assert healed["healed"] >= 1, healed                                            # the engine found the renamed field by its description
    old = json.loads((arts / first / "script.json").read_text())["steps"][1]["locator"]
    new = json.loads((arts / healed["run_id"] / "script.json").read_text())["steps"][1]["locator"]
    assert new != old                                                              # the script was updated with what it found
    (tmp_path / "healed").mkdir()
    _assert_scripts_run(tmp_path / "healed", arts / healed["run_id"])              # and runs on its own on the changed page

    # the same replay from the extension's own panel (the engine in the person's tab): its scripts work too
    known = {r["run_id"] for r in runs(base)}
    ctx, ext = browser
    close_extra(browser)
    tab = ctx.new_page()
    tab.goto(page_srv.url)
    tab.bring_to_front()
    pnl = ctx.new_page()
    pnl.goto(f"chrome-extension://{ext}/panel.html")
    ready(pnl)
    pnl.locator("#hubd summary").click()
    pnl.locator(f'#hublists button[data-id="{healed["run_id"]}"]').click()
    pnl.wait_for_selector("#banner:not([hidden])", timeout=90000)
    assert pnl.locator("#banner").get_attribute("class").split()[-1] == "passed", pnl.locator("#steps").inner_text()
    from_panel = new_extension_run(base, known)
    assert wait_run(base, from_panel)["status"] == "passed"
    time.sleep(1)
    (tmp_path / "panel").mkdir()
    _assert_scripts_run(tmp_path / "panel", arts / from_panel)
