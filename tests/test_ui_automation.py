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
def page(hub):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, executable_path=os.getenv("AIAUTO_CHROME_BINARY") or None)
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["clipboard-read", "clipboard-write"])
        pg = ctx.new_page()
        pg.set_default_timeout(15000)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        yield pg
        b.close()


@pytest.fixture(scope="module")
def seeded(hub):
    """A saved script, and run history that makes one test flaky."""
    s = {"name": "Home page", "base_url": None, "data": {}, "sensitive": [],
         "steps": [{"action": "navigate", "value": "about:blank", "raw": "Open the page"}]}
    call(hub, "/api/library", {"name": "home-page", "test": s})
    return "home-page"


def go(page, hub, hash_=""):
    page.goto(f"{hub}/#/{hash_}")
    page.wait_for_selector(".app")


def test_the_sidebar_has_an_automation_page_with_four_tabs(page, hub, seeded):
    go(page, hub, "dashboard")
    page.get_by_role("link", name="Automation").first.click()
    page.wait_for_selector("[role=tablist][aria-label=Automation]")
    assert [t.inner_text() for t in page.locator("[role=tablist][aria-label=Automation] button").all()] == ["Schedules", "CI hooks", "Alerts", "Flaky tests"]
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
    page.once("dialog", lambda d: d.accept())
    card.get_by_role("button", name="Delete").click()
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
        page.once("dialog", lambda d: d.accept())
        card.get_by_role("button", name="Remove").click()
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
