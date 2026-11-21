"""The Automation page, driven in a real browser against a real hub: schedules, CI hooks, alert channels, flaky tests,
and the places the rest of the app links to them (Library, Overview, Studio)."""
import json
import os
import time
import urllib.request

import pytest

from .conftest import EXAMPLES
from .test_extension import ROOT, _start_hub, _serve  # noqa: F401  (hub process helper)


def call(base, path, body=None, method=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", "X-Requested-With": "autopilot"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"{}")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    p, base = _start_hub(tmp_path_factory.mktemp("uihub"))
    yield base
    p.terminate()


@pytest.fixture(scope="module")
def chromium(hub):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, executable_path=os.getenv("AIAUTO_CHROME_BINARY") or None)
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(chromium):
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900}, permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.set_default_timeout(15000)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    yield pg
    ctx.close()


@pytest.fixture(scope="module")
def seeded(hub):
    """A saved script, and run history that makes one test flaky."""
    s = {"name": "Home page", "base_url": None, "data": {}, "sensitive": [],
         "steps": [{"action": "navigate", "value": "about:blank", "raw": "Open the page"}]}
    call(hub, "/api/library", {"name": "home-page", "test": s})
    return "home-page"


def sure(page, ok=True):
    """Answer the app's own confirmation dialog."""
    dlg = page.get_by_role("alertdialog")
    dlg.wait_for()
    dlg.get_by_role("button", name="Cancel" if not ok else dlg.locator("button").last.inner_text()).click()
    dlg.wait_for(state="detached")


def go(page, hub, hash_=""):
    page.goto(f"{hub}/#/{hash_}")
    page.wait_for_selector(".app")


def test_the_sidebar_has_an_automation_page_with_four_tabs(page, hub, seeded):
    go(page, hub, "dashboard")
    page.get_by_role("link", name="Automation").first.click()
    page.wait_for_selector("[role=tablist][aria-label=Automation]")
    assert [t.inner_text() for t in page.locator("[role=tablist][aria-label=Automation] button").all()] == ["Schedules", "CI hooks", "Alerts", "Flaky tests", "Baselines"]
    assert page.locator("h1").inner_text() == "Automation"


def test_a_schedule_is_made_previewed_paused_run_and_deleted(page, hub, seeded):
    go(page, hub, "automation/schedules")
    page.get_by_role("button", name="New schedule").click()
    form = page.get_by_role("form", name="Schedule")
    form.get_by_label("Name").fill("Nightly home")
    form.get_by_label("Script").select_option("home-page")
    form.get_by_label("How often").select_option("0 9 * * mon-fri")
    assert form.get_by_label("Cron expression").input_value() == "0 9 * * mon-fri"
    page.wait_for_selector("text=Next runs:")
    form.get_by_label("Cron expression").fill("nonsense")
    page.wait_for_selector("[role=alert]")
    assert form.get_by_role("button", name="Save schedule").is_disabled()           # a bad cron cannot be saved
    form.get_by_label("Cron expression").fill("*/20 * * * *")
    page.wait_for_selector("text=Next runs:")
    form.get_by_role("button", name="Save schedule").click()
    page.wait_for_selector(".card h4:has-text('Nightly home')")
    saved = call(hub, "/api/schedules")
    assert saved[0]["library"] == "home-page" and saved[0]["cron"] == "*/20 * * * *" and saved[0]["config"]["headless"] is True

    card = page.locator(".card", has_text="Nightly home")
    assert "next:" in card.inner_text()
    card.get_by_role("button", name="Pause").click()
    page.wait_for_selector(".card.off")
    assert call(hub, "/api/schedules")[0]["enabled"] is False
    card.get_by_role("button", name="Resume").click()
    page.wait_for_function("() => !document.querySelector('.card.off')")

    card.get_by_role("button", name="Run now").click()
    page.wait_for_selector("text=Open last run")
    assert call(hub, "/api/schedules")[0]["last_run_ids"]
    card.get_by_role("button", name="Delete").click()
    sure(page)
    page.wait_for_selector("text=No schedules yet")


def test_the_library_card_opens_a_schedule_for_that_script(page, hub, seeded):
    go(page, hub, "library")
    page.locator(".card", has_text="home-page").get_by_role("button", name="Schedule").click()
    page.wait_for_selector("[role=form][aria-label=Schedule]")
    assert page.get_by_role("form", name="Schedule").get_by_label("Script").input_value() == "home-page"


def test_a_ci_hook_shows_its_url_once_and_the_url_works(page, hub, seeded):
    go(page, hub, "automation/hooks")
    page.get_by_role("button", name="New hook").click()
    form = page.get_by_role("form", name="CI hook")
    form.get_by_label("Script").select_option("home-page")
    form.get_by_label("Name").fill("After deploy")
    form.get_by_role("button", name="Create hook").click()
    page.wait_for_selector("text=shown once")
    url = page.locator(".card code").first.inner_text()
    assert "/api/hooks/run/" in url and url.startswith(hub)
    page.get_by_role("button", name="Copy URL").click()
    assert page.evaluate("() => navigator.clipboard.readText()") == url
    ids = json.loads(urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})).read())["run_ids"]
    assert len(ids) == 1                                                                  # the URL starts the script, with no sign-in
    page.get_by_role("button", name="I've saved it").click()
    assert not page.locator("text=shown once").count()
    page.reload()
    page.wait_for_selector(".card h4:has-text('After deploy')")
    assert "/api/hooks/run/" not in page.locator("body").inner_text()                     # the secret is not shown again


def test_alert_channels_are_added_tested_and_removed(page, hub, seeded):
    import http.server, threading
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        go(page, hub, "automation/alerts")
        page.get_by_role("button", name="Add a channel").click()
        form = page.get_by_role("form", name="Alert channel")
        form.get_by_label("Where").select_option("slack")
        form.get_by_label("Tell me").select_option("always")
        assert form.get_by_role("button", name="Save channel").is_disabled()
        form.get_by_label("Webhook URL").fill(f"http://127.0.0.1:{srv.server_port}/slack")
        form.get_by_role("button", name="Save channel").click()
        card = page.locator(".card", has=page.locator("h4", has_text="Slack"))
        card.wait_for()
        assert "after every run" in card.inner_text()
        card.get_by_role("button", name="Send test").click()
        page.wait_for_selector("text=Test message sent")
        assert got and "only a test" in got[0]["text"]
        card.get_by_role("button", name="Remove").click()
        sure(page)
        page.wait_for_selector("text=No alert channels")
        page.get_by_role("button", name="Add a channel").click()
        page.get_by_role("form", name="Alert channel").get_by_label("Where").select_option("email")
        assert page.get_by_label("Send to").is_visible() and not page.get_by_label("Webhook URL").count()
    finally:
        srv.shutdown()


def test_flaky_tests_are_listed_and_the_overview_points_at_them(page, hub, seeded, tmp_path_factory):
    # make history: a script that passes, then fails, then passes, ... (the page it opens changes)
    d = tmp_path_factory.mktemp("flaky")
    state = d / "page.html"
    srv, url = _serve(d)
    try:
        script = {"name": "Wobbly", "base_url": None, "data": {}, "sensitive": [],
                  "steps": [{"action": "navigate", "value": url + "/page.html", "raw": "Open"}, {"action": "assert_text", "value": "FINE", "raw": 'Verify the page shows "FINE"'}]}
        call(hub, "/api/library", {"name": "wobbly", "test": script})
        cfg = {"engine": "playwright", "record_video": False, "use_llm": False, "timeout_ms": 4000, "headless": True}
        for text in ("FINE", "BAD", "FINE", "BAD", "FINE"):
            state.write_text(f"<html><body>{text}</body></html>")
            rid = call(hub, "/api/runs", {"library": "wobbly", "config": cfg})["run_ids"][0]
            end = time.time() + 60
            while time.time() < end and next(r for r in call(hub, "/api/runs") if r["run_id"] == rid)["status"] in ("queued", "dispatched", "running"):
                time.sleep(0.4)
    finally:
        srv.shutdown()
    rows = call(hub, "/api/flaky?only=true")
    assert [r["name"] for r in rows] == ["Wobbly"]
    go(page, hub, "automation/flaky")
    page.wait_for_selector("tr:has-text('Wobbly')")
    row = page.locator("tr", has_text="Wobbly")
    assert "flaky" in row.inner_text().lower() and row.locator(".dots i").count() == 5 and row.locator(".dots i.f").count() == 2
    page.locator("label.toggle", has_text="Only flaky tests").click()
    page.wait_for_selector("tr:has-text('Wobbly')")
    go(page, hub, "dashboard")
    page.wait_for_selector("h3:has-text('Flaky tests')")
    assert "Wobbly" in page.locator("section.panel", has_text="Flaky tests").inner_text()
    page.get_by_role("button", name="See all").click()
    page.wait_for_selector("[role=tab][aria-selected=true]:has-text('Flaky tests')")


def test_studio_offers_the_new_steps_and_the_baseline_toggle(page, hub, seeded):
    go(page, hub, "studio")
    page.locator(".tabs button", has_text="Settings").click()
    page.locator("label.toggle", has_text="Accept the new look in visual checks").click()
    assert page.evaluate("() => JSON.parse(localStorage.getItem('aiauto.form2')).updateBaselines") is True
    page.locator("label.toggle", has_text="Accept the new look in visual checks").click()
    assert not page.errors, page.errors


# ------------------------------------------------------------------------------------------ the weak spots, fixed
def test_schedules_show_the_clock_a_summary_and_how_the_last_runs_went(page, hub, seeded):
    go(page, hub, "automation/schedules")
    page.get_by_role("button", name="New schedule").click()
    form = page.get_by_role("form", name="Schedule")
    form.get_by_label("Script").select_option("home-page")
    form.get_by_role("button", name="Save schedule").click()
    card = page.locator(".card", has=page.locator("h4", has_text="home-page"))
    card.wait_for()
    assert page.locator("p.hint", has_text="Times use the hub's clock").is_visible()
    assert page.get_by_role("group", name="Summary").inner_text().replace("\n", " ").count("active") == 1
    assert card.locator(".dotc[aria-label='not run yet']").count() == 1              # never run: a grey dot with a name for screen readers
    for _ in range(2):
        card.get_by_role("button", name="Run now").click()
        page.wait_for_selector("text=Started")
        time.sleep(0.3)
    card.locator(".dots i").first.wait_for(timeout=30000)
    page.wait_for_function("() => document.querySelectorAll('.card .dots i').length >= 2", timeout=30000)
    assert card.locator(".dots").get_attribute("aria-label").startswith("Recent launches")
    page.wait_for_selector(".card .dotc.ok, .card .dotc.bad", timeout=60000)            # the dot takes the colour of the last launch
    card.get_by_role("button", name="Delete").click()
    sure(page)


def test_a_cancelled_confirmation_changes_nothing(page, hub, seeded):
    go(page, hub, "automation/schedules")
    page.get_by_role("button", name="New schedule").click()
    form = page.get_by_role("form", name="Schedule")
    form.get_by_label("Script").select_option("home-page")
    form.get_by_label("Name").fill("Keep me")
    form.get_by_role("button", name="Save schedule").click()
    card = page.locator(".card", has=page.locator("h4", has_text="Keep me"))
    card.get_by_role("button", name="Delete").click()
    dlg = page.get_by_role("alertdialog")
    assert dlg.get_by_role("button", name="Cancel").evaluate("e => e === document.activeElement")      # focus starts on the safe choice
    page.keyboard.press("Escape")
    dlg.wait_for(state="detached")
    assert card.is_visible() and [s["name"] for s in call(hub, "/api/schedules")] == ["Keep me"]
    card.get_by_role("button", name="Delete").click()
    sure(page)


def test_a_channel_can_be_tried_before_it_is_saved(page, hub, seeded):
    import http.server, threading
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        go(page, hub, "automation/alerts")
        page.get_by_role("button", name="Add a channel").click()
        form = page.get_by_role("form", name="Alert channel")
        assert form.get_by_role("button", name="Send a test first").is_disabled()
        form.get_by_label("Webhook URL").fill(f"http://127.0.0.1:{srv.server_port}/x")
        form.get_by_role("button", name="Send a test first").click()
        page.wait_for_selector("text=Test message sent")
        assert got and "only a test" in got[0]["text"] and call(hub, "/api/notifications") == []      # sent, and nothing saved
        form.get_by_role("button", name="Cancel").click()
    finally:
        srv.shutdown()


def test_the_tabs_work_from_the_keyboard(page, hub, seeded):
    go(page, hub, "automation/schedules")
    page.get_by_role("tab", name="Schedules").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("[role=tab][aria-selected=true]:has-text('CI hooks')")
    assert page.evaluate("() => document.activeElement.textContent") == "CI hooks"
    page.keyboard.press("End")
    page.wait_for_selector("[role=tab][aria-selected=true]:has-text('Baselines')")
    page.keyboard.press("ArrowRight")                                                       # wraps round
    page.wait_for_selector("[role=tab][aria-selected=true]:has-text('Schedules')")
    assert page.get_by_role("tab", name="Alerts").get_attribute("tabindex") == "-1"           # one tab stop for the whole list


def test_flaky_tests_can_be_searched_and_sorted(page, hub, seeded):
    go(page, hub, "automation/flaky")
    page.wait_for_selector("table")
    page.get_by_placeholder("Search tests…").fill("zzz-nothing")
    page.wait_for_selector("text=No test matches that.")
    page.get_by_placeholder("Search tests…").fill("")
    page.get_by_label("Sort").select_option("name")
    page.wait_for_selector("table")


def test_baselines_are_shown_in_the_app_and_a_changed_look_is_accepted_from_the_run(page, hub, seeded, tmp_path_factory):
    d = tmp_path_factory.mktemp("look")
    pg = d / "p.html"
    srv, url = _serve(d)
    try:
        script = {"name": "Look", "base_url": None, "data": {}, "sensitive": [],
                  "steps": [{"action": "navigate", "value": url + "/p.html", "raw": "Open"}, {"action": "assert_visual", "value": "front", "raw": "Verify the page looks the same as front"}]}
        call(hub, "/api/library", {"name": "look", "test": script})
        cfg = {"engine": "playwright", "record_video": False, "use_llm": False, "timeout_ms": 6000, "headless": True}

        def go_run(color):
            pg.write_text(f'<html><body style="margin:0"><div style="width:300px;height:200px;background:{color}"></div></body></html>')
            rid = call(hub, "/api/runs", {"library": "look", "config": cfg})["run_ids"][0]
            end = time.time() + 60
            while time.time() < end and next(r for r in call(hub, "/api/runs") if r["run_id"] == rid)["status"] in ("queued", "dispatched", "running"):
                time.sleep(0.4)
            return rid
        go_run("#3366cc")
        go(page, hub, "automation/baselines")
        page.wait_for_selector("figure.thumb img")
        assert page.locator("figure.thumb figcaption b").inner_text() == "front"
        page.wait_for_function("() => { const i = document.querySelector('figure.thumb img'); return i && i.complete && i.naturalWidth > 0; }")

        bad = go_run("#cc3333")
        go(page, hub, f"runs/{bad}")
        page.wait_for_selector(".srow.failed")
        page.locator(".srow", has_text="looks the same").click()
        region = page.get_by_role("region", name="Visual check")
        region.wait_for()
        assert "looks different" in region.inner_text().lower() and region.locator("img").count() == 3          # baseline, this run, the difference
        region.get_by_role("button", name="Accept the new look").click()
        sure(page)
        page.wait_for_selector("text=Baseline updated")
        assert go_run("#cc3333") and next(r for r in call(hub, "/api/runs"))["status"] == "passed"
        go(page, hub, "automation/baselines")
        page.locator("figure.thumb").get_by_role("button", name="Delete").click()
        sure(page)
        page.wait_for_selector("text=No baselines yet")
    finally:
        srv.shutdown()


def test_api_and_mock_steps_get_real_fields_not_a_json_box(page, hub, seeded):
    go(page, hub, "studio")
    page.evaluate("""() => { const f = JSON.parse(localStorage.getItem('aiauto.form2') || '{}');
      f.text = 'x'; f.stepsText = 'x'; f.steps = [{action: 'api', target: 'POST /api/orders', value: null, source: 'user', raw: 'call'},
        {action: 'mock', target: '/api/items', value: '{"status": 500}', source: 'user', raw: 'mock'},
        {action: 'assert_visual', target: null, value: 'home', source: 'user', raw: 'look'},
        {action: 'assert_accessible', target: null, value: null, source: 'user', raw: 'a11y'}];
      localStorage.setItem('aiauto.form2', JSON.stringify(f)); }""")
    page.reload()
    page.wait_for_selector(".guided")
    api_box = page.get_by_label("API call")
    api_box.get_by_label("Expect status").fill("201")
    api_box.get_by_label("Answer contains").fill("created")
    api_box.get_by_label("Body (JSON)").fill('{"sku": 7}')
    api_box.get_by_label("Save from answer").fill("id=data.id")
    state = page.evaluate("() => JSON.parse(localStorage.getItem('aiauto.form2')).steps")
    assert json.loads(state[0]["value"]) == {"expect_status": 201, "expect_contains": "created", "body": {"sku": 7}, "save": {"id": "data.id"}}
    assert state[0]["target"].startswith("POST ")
    page.get_by_label("Network rule").get_by_label("What to do").select_option("block")
    state = page.evaluate("() => JSON.parse(localStorage.getItem('aiauto.form2')).steps")
    assert json.loads(state[1]["value"]) == {"block": True}
    page.get_by_label("Visual check").get_by_label("Allowed difference (%)").fill("0.5")
    page.get_by_label("Accessibility scan").get_by_label("Fail on").select_option("critical")
    state = page.evaluate("() => JSON.parse(localStorage.getItem('aiauto.form2')).steps")
    assert state[2]["value"] == "home:0.5" and state[3]["value"] == "critical"
    assert not page.errors, page.errors


def test_a_new_user_gets_a_checklist_that_ticks_itself(chromium, tmp_path_factory):
    p, base = _start_hub(tmp_path_factory.mktemp("fresh"))
    try:
        if True:
            pg = chromium.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(base + "/#/dashboard")
            box = pg.get_by_role("region", name="Getting started")
            box.wait_for()
            assert "0 of 5" in box.inner_text()
            box.get_by_role("button", name="Try an example").click()
            pg.wait_for_selector("h1:has-text('Studio')")
            assert "The visitor" in pg.evaluate("() => JSON.parse(localStorage.getItem('aiauto.form2')).text")          # the example is in the editor
            s = {"name": "Home", "data": {}, "sensitive": [], "steps": [{"action": "navigate", "value": "about:blank", "raw": "Open"}]}
            call(base, "/api/library", {"name": "home", "test": s})
            call(base, "/api/schedules", {"library": "home", "cron": "0 3 * * *"}, method="PUT")
            pg.goto(base + "/#/dashboard")
            pg.reload()
            box = pg.get_by_role("region", name="Getting started")
            box.wait_for()
            pg.wait_for_function("() => document.querySelectorAll('.gs li.done').length >= 3")      # wrote a test (library), kept a script, scheduled it
            box.get_by_role("button", name="Hide").click()
            pg.reload()
            pg.wait_for_selector("h1:has-text('Overview')")
            assert pg.get_by_role("region", name="Getting started").count() == 0                   # and it stays hidden
            pg.close()
    finally:
        p.terminate()


def test_the_page_holds_up_on_a_phone_and_in_dark_mode(chromium, hub, seeded):
    if True:
        b = chromium
        for scheme, w in (("dark", 1280), ("light", 390), ("dark", 390)):
            ctx = b.new_context(viewport={"width": w, "height": 800}, color_scheme=scheme)
            pg = ctx.new_page()
            for tab in ("schedules", "hooks", "alerts", "flaky", "baselines"):
                pg.goto(f"{hub}/#/automation/{tab}")
                pg.wait_for_selector("[role=tabpanel]")
                pg.wait_for_timeout(250)
                assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), (scheme, w, tab)     # no sideways scrolling
            pg.goto(f"{hub}/#/automation/schedules")
            pg.get_by_role("button", name="New schedule").click()
            pg.get_by_role("form", name="Schedule").wait_for()
            assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), (scheme, w, "form")
            bg = pg.evaluate("() => getComputedStyle(document.querySelector('.formcard')).backgroundColor")
            if scheme == "dark":
                r, g, b_ = [int(x) for x in bg.replace("rgb(", "").replace(")", "").split(",")[:3]]
                assert r + g + b_ < 300, bg                                                         # a dark card in dark mode
            ctx.close()
