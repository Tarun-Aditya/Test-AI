"""The Chrome extension, in a real Chromium: it is loaded unpacked next to a hub, a scenario is pasted into its panel, and the
page under test is checked afterwards. Skipped where this Chromium can't load extensions."""
import functools
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from .conftest import EXAMPLES

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(directory):
    port = _free_port()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def sites():
    a, site = _serve(EXAMPLES)
    b, xsite = _serve(EXAMPLES / "xorigin")           # its outer page embeds an iframe from localhost (another origin)
    yield {"site": site, "xsite": xsite}
    a.shutdown()
    b.shutdown()


def _start_hub(tmp_path, auth=False):
    port = _free_port()
    env = {**os.environ, "AIAUTO_AUTH": "on" if auth else "off", "AIAUTO_DISABLE_LLM": "1", "AIAUTO_DB": str(tmp_path / "hub.db"),
           "AIAUTO_ARTIFACTS": str(tmp_path / "art"), "ANTHROPIC_API_KEY": "",
           **({"AIAUTO_ADMIN_USER": "admin", "AIAUTO_ADMIN_PASSWORD": "autopilot-demo"} if auth else {})}
    p = subprocess.Popen([sys.executable, "-m", "aiauto", "server", "--host", "127.0.0.1", "--port", str(port)], env=env, cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            return p, base
        except Exception:
            time.sleep(0.25)
    p.kill()
    raise RuntimeError("the hub did not start")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    p, base = _start_hub(tmp_path_factory.mktemp("hub"))
    yield base
    p.terminate()


@pytest.fixture(scope="module")
def browser(tmp_path_factory, hub):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    prof = tmp_path_factory.mktemp("profile")
    ctx = pw.chromium.launch_persistent_context(
        str(prof), headless=False, executable_path=os.getenv("AIAUTO_CHROME_BINARY") or None, viewport={"width": 1100, "height": 800},
        args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--no-sandbox"])
    try:
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    except Exception:
        ctx.close()
        pw.stop()
        pytest.skip("this Chromium did not load the unpacked extension")
    ext = sw.url.split("/")[2]
    sw.evaluate("(hub) => chrome.storage.local.set({hub})", hub)
    ctx.on("dialog", lambda d: None)        # Playwright dismisses dialogs nobody handles; the extension under test handles them
    yield ctx, ext
    ctx.close()
    pw.stop()


def ready(panel, timeout=15):
    """The panel has found its tab. Extension pages forbid string evaluation, so poll the text rather than evaluate a predicate."""
    end = time.time() + timeout
    while time.time() < end:
        if panel.locator("#ttitle").inner_text() != "Looking for your tab…":
            return
        time.sleep(0.1)
    raise AssertionError("the panel never found its tab")


def run_scenario(browser, url, text, data="", wait=60, expect_banner=True):
    """Open `url` in a tab, paste `text` into the panel, run it. Returns (banner status, steps, panel, page)."""
    ctx, ext = browser
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
    status = None
    if expect_banner:
        panel.wait_for_selector("#banner:not([hidden])", timeout=wait * 1000)
        status = panel.locator("#banner").get_attribute("class").split()[-1]
    steps = [(li.get_attribute("class").split()[-1], li.locator(".tx").inner_text(), li.locator(".err").inner_text() if li.locator(".err").count() else "")
             for li in panel.locator("#steps li").all()]
    return status, steps, panel, page


def close_extra(browser):
    ctx, _ = browser
    for p in ctx.pages[1:]:
        p.close()


def test_a_business_paragraph_runs_on_the_open_page_and_follows_the_new_tab_it_opens(browser, sites):
    ctx, _ = browser
    status, steps, panel, page = run_scenario(
        browser, sites["site"] + "/extension/newtab.html",
        "The visitor clicks the Open the order form link. In the new tab, the visitor enters the name Asha, selects 2 as the quantity and "
        "clicks Place order. The page should show Thanks, Asha.")
    assert status == "passed", steps
    assert panel.locator("#banner").inner_text().count("2 tabs") == 1          # the panel says it followed the scenario into a second tab
    order = next(p for p in ctx.pages if p.url.endswith("/order.html"))
    assert "Thanks, Asha! Order placed for 2 ticket(s)." in order.locator("#out").inner_text()
    assert page.url.endswith("/newtab.html")                                    # the first tab is where we left it
    close_extra(browser)


def test_window_open_from_a_click_is_followed(browser, sites):
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/extension/newtab.html",
                                              "The visitor clicks the Quick help button. In the new tab the page should show Call us any time on 555-0100.")
    assert status == "passed", steps                                            # a real click is a user gesture: the popup is allowed
    close_extra(browser)


def test_hover_is_a_real_hover(browser, sites):
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/extension/hover.html",
                                              "The visitor hovers over the avatar card. The page should show Name: Asha Rao.")
    assert status == "passed", steps                                            # the caption exists only while the mouse is really over the card
    close_extra(browser)


def test_native_dialogs_are_answered(browser, sites):
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/extension/dialog.html",
                                              "The user clicks the Delete account button. The page should show Account deleted.")
    assert status == "passed", steps
    close_extra(browser)


def test_a_signup_paragraph_with_a_password_nobody_gave(browser, sites):
    status, steps, panel, page = run_scenario(
        browser, sites["site"] + "/signup/signup.html",
        "The visitor lands on the sign up page and enters a valid email address test.user@example.com, a username testuser, age 30 and a "
        "password of at least 8 characters, then submits the registration form. The system should confirm that the account was created successfully.")
    assert status == "passed", steps
    assert page.locator("#email").input_value() == "test.user@example.com"
    assert "Account created" in page.content()
    assert page.url.endswith("/signup/signup.html")                             # "lands on the sign up page" is the page you are on: no reload
    close_extra(browser)


def test_a_multi_page_flow_in_one_tab(browser, sites):
    status, steps, panel, page = run_scenario(
        browser, sites["site"] + "/shop/index.html",
        "As a customer I want to buy a wireless mouse. I navigate to the DemoShop home page, search for wireless mouse and select the first "
        "Logitech product from the results. I choose quantity 2 and add the item to my cart. When I open the cart, the subtotal should show 2 items.")
    assert status == "passed", steps
    assert page.url.endswith("/shop/cart.html")
    close_extra(browser)


def test_a_cross_origin_iframe(browser, sites):
    status, steps, panel, page = run_scenario(browser, sites["xsite"] + "/outer.html",
                                              "The visitor enters the card number 4111111111114242 and clicks Pay securely. The page should show Paid with 4242.")
    assert status == "passed", steps
    close_extra(browser)


def test_closed_shadow_roots_are_opt_in(browser, sites):
    ctx, ext = browser
    scenario = "The visitor enters the promo code SAVE10 and clicks Apply promo. The page should show Promo SAVE10 applied."
    status, steps, panel, page = run_scenario(browser, sites["xsite"] + "/outer.html", scenario, wait=40)
    assert status == "failed" and any("promo" in e.lower() for _, _, e in steps)          # nothing can see inside a closed shadow root
    close_extra(browser)
    opt = ctx.new_page(); opt.goto(f"chrome-extension://{ext}/options.html")
    opt.locator("#shadow").check()
    opt.locator("#sstatus", has_text="On").wait_for(timeout=10000)
    try:
        status, steps, panel, page = run_scenario(browser, sites["xsite"] + "/outer.html", scenario)          # loaded after the option was turned on
        assert status == "passed", steps
    finally:
        close_extra(browser)
        opt = ctx.new_page(); opt.goto(f"chrome-extension://{ext}/options.html")
        opt.locator("#shadow").uncheck()
        opt.locator("#sstatus", has_text="Off").wait_for(timeout=10000)
        opt.close()


def test_a_secret_typed_into_the_page_is_masked_and_never_shown_in_the_panel(browser, sites):
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/signup/signup.html",
                                              "The visitor enters age 30 and the password Zz9!secretvalue then submits it.")
    assert status in ("passed", "failed")
    assert "Zz9!secretvalue" not in panel.locator("#steps").inner_text() and "Zz9!secretvalue" not in panel.content()
    assert page.locator("#password").input_value() == "Zz9!secretvalue"
    close_extra(browser)


def test_a_step_that_cannot_find_its_element_fails_with_what_it_saw(browser, sites):
    status, steps, panel, page = run_scenario(browser, sites["site"] + "/extension/hover.html", "Click the Unicorn button.", wait=40)
    assert status == "failed"
    assert any("Could not identify element for 'Unicorn button'" in e for _, _, e in steps)
    close_extra(browser)


def test_stop_ends_a_run_after_the_current_step(browser, sites):
    ctx, ext = browser
    page = ctx.new_page(); page.goto(sites["site"] + "/extension/hover.html"); page.bring_to_front()
    panel = ctx.new_page(); panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#text").fill("Wait 30 seconds\nHover over the avatar card")
    panel.locator("#run").click()
    panel.wait_for_selector("#stop:not([hidden])")
    time.sleep(1)
    panel.locator("#stop").click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=15000)
    assert "cancelled" in panel.locator("#banner").get_attribute("class")
    close_extra(browser)


def test_the_panel_says_what_to_do_when_there_is_no_website_tab(browser):
    ctx, ext = browser
    for p in ctx.pages[1:]:
        p.close()
    panel = ctx.new_page(); panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#text").fill("Click Login")
    panel.locator("#run").click()
    panel.wait_for_selector("#msg:not([hidden])", timeout=10000)
    assert "Open the website you want to test" in panel.locator("#msg").inner_text() or "No website tab" in panel.locator("#ttitle").inner_text()
    close_extra(browser)


def test_signing_in_to_a_hub_with_accounts(browser, sites, tmp_path):
    """The session cookie set by the sign-in travels with the extension's own requests (and the CSRF header is accepted)."""
    ctx, ext = browser
    p, base = _start_hub(tmp_path, auth=True)
    try:
        sw = ctx.service_workers[0]
        old = sw.evaluate("async () => (await chrome.storage.local.get('hub')).hub")
        sw.evaluate("(hub) => chrome.storage.local.set({hub})", base)
        page = ctx.new_page(); page.goto(sites["site"] + "/signup/signup.html"); page.bring_to_front()
        panel = ctx.new_page(); panel.goto(f"chrome-extension://{ext}/panel.html")
        ready(panel)
        panel.locator("#text").fill("The visitor enters the age 30."); panel.locator("#run").click()
        panel.wait_for_selector("#msg:not([hidden])", timeout=10000)
        assert "Sign in to the hub" in panel.locator("#msg").inner_text()
        opt = ctx.new_page(); opt.goto(f"chrome-extension://{ext}/options.html")
        opt.fill("#u", "admin"); opt.fill("#p", "autopilot-demo"); opt.click("#login")
        opt.locator("#lstatus", has_text="Signed in").wait_for(timeout=10000)
        panel.bring_to_front(); panel.locator("#run").click()
        panel.wait_for_selector("#banner:not([hidden])", timeout=30000)
        assert "passed" in panel.locator("#banner").get_attribute("class") and page.locator("#age").input_value() == "30"
        sw.evaluate("(hub) => chrome.storage.local.set({hub})", old)
    finally:
        p.terminate()
        close_extra(browser)


# ------------------------------------------------------------------------------------------------ not browser tests
def test_the_hubs_locate_endpoint_picks_like_the_engines(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("AIAUTO_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("AIAUTO_ARTIFACTS", str(tmp_path / "a"))
    import aiauto.server.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    base = {"frames": [], "shadow": [], "anc": [], "label": "", "placeholder": "", "aria_label": "", "nameattr": "", "title": "", "alt": "",
            "value": "", "href": "", "testid": "", "cls": "", "editable": False, "options": [], "visible": True, "id": "", "text": ""}
    cands = [{**base, "uid": 1, "tag": "input", "type": "text", "role": "textbox", "name": "Email", "label": "Email"},
             {**base, "uid": 2, "tag": "button", "type": "submit", "role": "button", "name": "Create account", "text": "Create account"}]
    with TestClient(appmod.app) as c:
        h = {"X-Requested-With": "autopilot"}
        def ask(step):
            return c.post("/api/locate", json={"step": step, "candidates": json.loads(json.dumps(cands))}, headers=h).json()
        assert ask({"action": "type", "target": "Email", "value": "a@b.c"})["uid"] == 1
        assert ask({"action": "click", "target": "submit button"})["uid"] == 2          # "submit button" names the control by its type
        r = ask({"action": "click", "target": "Unicorn"})
        assert r["uid"] is None and r["problem"] and r["near"]
        assert c.post("/api/locate", json={"step": {"action": "nonsense"}, "candidates": []}, headers=h).status_code == 400


def test_the_generated_page_library_is_up_to_date():
    """extension/lib/shared.js comes from the Python engines' own scripts: run tools/build_extension.py after changing them."""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_extension.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_manifest_asks_for_what_the_extension_uses():
    m = json.loads((EXT / "manifest.json").read_text())
    assert m["manifest_version"] == 3 and m["background"]["service_worker"] == "background.js"
    assert {"debugger", "scripting", "tabs", "storage", "sidePanel"} <= set(m["permissions"])
    for f in [m["background"]["service_worker"], m["side_panel"]["default_path"], m["options_page"], *m["icons"].values(),
              "lib/runner.js", "lib/cdp.js", "lib/hub.js", "lib/page.js", "lib/shared.js", "panel.js", "options.js", "panel.css"]:
        assert (EXT / f).is_file(), f


# ---------------------------------------------------------------------------------------------- the recorder
def _record_order_flow(browser, sites):
    """Open the order form, record a person using it (real input), stop. Returns (panel, page)."""
    ctx, ext = browser
    close_extra(browser)
    page = ctx.new_page()
    page.goto(sites["site"] + "/extension/order.html")
    page.bring_to_front()
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#mrec").click()
    panel.locator("#recstart").click()
    panel.wait_for_selector("#recstop:not([hidden])")
    page.click("#name")
    page.keyboard.type("Asha")
    page.focus("#qty")
    page.keyboard.press("ArrowDown")                 # a real key press (select_option sends untrusted events)
    page.check("#news")
    page.click("#go")
    page.wait_for_selector("#out:has-text('Thanks, Asha')")
    panel.locator("#checktext").fill("Thanks, Asha")
    panel.locator("#checkadd").click()
    time.sleep(1.2)
    panel.locator("#recstop").click()
    panel.wait_for_selector("#recout:not([hidden])")
    return panel, page


def test_recording_a_flow_makes_steps_and_replaying_them_does_it_again(browser, sites):
    panel, page = _record_order_flow(browser, sites)
    steps = [li.inner_text() for li in panel.locator("#recsteps li").all()]
    text = "\n".join(steps)
    for want in ('type "Asha" into', 'select "2" from', "check Send me the newsletter", "click Place order", 'verify the page shows "Thanks, Asha"'):
        assert want in text, text
    assert "finds it by" in text                       # each step remembers where the element was
    page.goto(page.url)                                # fresh page: the order is not placed yet
    assert page.locator("#out").inner_text() == ""
    page.bring_to_front()
    panel.locator("#recreplay").click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=60000)
    assert panel.locator("#banner").get_attribute("class").split()[-1] == "passed", panel.locator("#steps").inner_text()
    assert "recorded" in panel.locator("#steps").inner_text()           # found by the stored locator, not by guessing
    assert "Thanks, Asha" in page.locator("#out").inner_text()
    assert page.locator("#news").is_checked()


def test_a_recording_still_replays_when_the_page_changed_its_ids(browser, sites):
    panel, page = _record_order_flow(browser, sites)
    page.goto(sites["site"] + "/extension/order2.html")                 # same labels and texts, every id changed
    page.bring_to_front()
    panel.locator("#recreplay").click()
    panel.wait_for_selector("#banner:not([hidden])", timeout=60000)
    assert panel.locator("#banner").get_attribute("class").split()[-1] == "passed", panel.locator("#steps").inner_text()
    assert "Thanks, Asha" in page.locator("#out").inner_text()


def test_a_recording_is_saved_to_the_hub_without_its_secret_and_a_chrome_recording_can_be_imported(browser, sites, hub):
    ctx, ext = browser
    close_extra(browser)
    page = ctx.new_page()
    page.goto(sites["site"] + "/extension/order.html")
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#mrec").click()
    rec = {"title": "Imported flow", "steps": [
        {"type": "navigate", "url": sites["site"] + "/extension/order.html"},
        {"type": "change", "value": "hunter2-secret", "selectors": [["aria/Password"], ["#pw"]]},
        {"type": "click", "selectors": [["aria/Place order[role=\"button\"]"], ["#go"]]}]}
    f = Path(ROOT / "dist" / "_imported.json")
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(rec))
    try:
        panel.locator("#chromefile").set_input_files(str(f))
        panel.wait_for_selector("#recout:not([hidden])", timeout=15000)         # the page reads the file lazily: keep it until then
    finally:
        f.unlink(missing_ok=True)
    assert "Imported flow" == panel.locator("#recname").input_value()
    assert panel.locator("#recsteps li").count() == 3
    assert "hunter2" not in panel.locator("#recsteps").inner_text()
    panel.locator("#recsave").click()
    time.sleep(1)
    saved = json.loads(urllib.request.urlopen(hub + "/api/library/Imported-flow").read())
    assert len(saved["steps"]) == 3 and saved["sensitive"] == ["password"] and saved["data"]["password"] == ""
