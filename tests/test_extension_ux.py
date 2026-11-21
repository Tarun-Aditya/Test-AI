"""The panel from a person's side: first-run setup, safety prompts, fixing a failed step, editing a recording, run controls, names, results in
the panel, history, and accessibility (labels, live announcements, dialogs, keyboard, dark mode, reduced motion)."""
import json
import time

import pytest

from .test_extension import close_extra, ready, sites  # noqa: F401
from .test_extension_check_replay import ORDER as ORDER_PAGE, record
from .test_extension_server import _Page, _start_hub, api, browser, rhub, runs, wait_run  # noqa: F401

ORDER = ("The visitor enters the name Asha, selects 2 as the quantity and clicks Place order. The page should show Thanks, Asha.")


def open_panel(browser, url=None):
    ctx, ext = browser
    close_extra(browser)
    page = None
    if url:
        page = ctx.new_page()
        page.goto(url)
        page.bring_to_front()
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    if panel.locator("#runctl").is_visible():                      # a run left over from an earlier test: stop it
        panel.locator("#stop").click()
        panel.locator("#runctl").wait_for(state="hidden", timeout=60000)
    return panel, page


def storage(browser, **kw):
    browser[0].sw.evaluate("(kw) => chrome.storage.local.set(kw)", kw)


def clear_storage(browser, *keys):
    browser[0].sw.evaluate("(keys) => chrome.storage.local.remove(keys)", list(keys))


def banner(panel, timeout=90):
    panel.wait_for_selector("#banner:not([hidden])", timeout=timeout * 1000)
    return panel.locator("#banner").get_attribute("class").split()[-1]


# ---------------------------------------------------------------------------------------------- first run
def test_the_setup_card_connects_to_a_hub_and_signs_in(browser, rhub, tmp_path):
    hub = rhub[0]
    storage(browser, hub="http://127.0.0.1:9")
    try:
        panel, _ = open_panel(browser)
        panel.wait_for_selector("#setup:not([hidden])")
        assert "Can't reach" in panel.locator("#setupmsg").inner_text() or "reach" in panel.locator("#setupmsg").inner_text().lower()
        panel.locator("#hubaddr").fill(hub)
        panel.locator("#hubconnect").click()
        panel.wait_for_selector("#setup", state="hidden", timeout=15000)
        assert "connected" in panel.locator("#hubline").inner_text()
        # a hub with accounts: the card asks for a sign-in and the sign-in happens in the panel
        p, base = _start_hub(tmp_path, auth=True)
        try:
            panel.locator("#gear").evaluate("() => {}")
            storage(browser, hub="http://127.0.0.1:9")
            panel.reload()
            ready(panel)
            panel.wait_for_selector("#setup:not([hidden])")
            panel.locator("#hubaddr").fill(base)
            panel.locator("#hubconnect").click()
            panel.wait_for_selector("#signin:not([hidden])", timeout=15000)
            panel.locator("#suser").fill("admin")
            panel.locator("#spass").fill("autopilot-demo")
            panel.locator("#slogin").click()
            panel.wait_for_selector("#setup", state="hidden", timeout=15000)
        finally:
            p.terminate()
    finally:
        storage(browser, hub=hub)


def test_the_welcome_card_and_the_debugging_bar_note_appear_once(browser, sites):
    clear_storage(browser, "welcomed", "seenBarNote")
    panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
    try:
        panel.wait_for_selector("#welcome:not([hidden])")
        assert "debugging this browser" in panel.locator("#welcome").inner_text()
        panel.locator("#welcomeok").click()
        panel.reload()
        ready(panel)
        assert panel.locator("#welcome").is_hidden()                                  # once
        panel.locator("#text").fill(ORDER)
        panel.locator("#run").click()
        panel.wait_for_selector("#barnote:not([hidden])", timeout=20000)
        banner(panel)
        panel.locator("#barok").click()
        assert panel.locator("#barnote").is_hidden()
    finally:
        storage(browser, welcomed=True, seenBarNote=True)


# ---------------------------------------------------------------------------------------------- data
def test_the_data_table_writes_the_text_and_hides_secrets(browser):
    panel, _ = open_panel(browser)
    panel.locator("#datad > summary").click()
    panel.locator("#dataadd").click()
    panel.locator("#datatable .dk").first.fill("password")
    panel.locator("#datatable .dv").first.fill("hunter2")
    panel.locator("#datatable .ds").first.check()
    assert panel.locator("#data").input_value() == "password* = hunter2"
    assert panel.locator("#datatable .dv").first.get_attribute("type") == "password"       # a secret is not shown
    panel.locator("#data").fill("city = Hyderabad\npin* = 1234")                              # and the text edits the table
    assert panel.locator("#datatable tbody tr").count() == 2
    panel.locator("#datatable .x").first.click()
    assert panel.locator("#data").input_value() == "pin* = 1234"


# ---------------------------------------------------------------------------------------------- safety
def test_it_asks_before_running_on_a_new_site_and_before_things_that_are_hard_to_undo(browser, sites):
    storage(browser, askBeforeRunning=True, localAllowed=False)
    try:
        panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
        panel.locator("#text").fill(ORDER)
        panel.locator("#run").click()
        panel.wait_for_selector("#confirm:not([hidden])")
        assert "Run on this site" in panel.locator("#confirmh").inner_text()
        panel.keyboard.press("Escape")                                                    # Escape cancels
        assert panel.locator("#confirm").is_hidden() and panel.locator("#banner").is_hidden()
        assert page.locator("#out").inner_text() == ""                                    # nothing ran
        panel.locator("#run").click()
        panel.locator("#confirmcheck").check()                                            # always allow this site
        panel.locator("#confirmyes").click()
        panel.wait_for_selector("#confirmh:has-text('hard to undo')")                     # "place order" is one of those
        assert "Place order" in panel.locator("#confirmb").inner_text()
        panel.locator("#confirmno").click()
        assert panel.locator("#banner").is_hidden()
        panel.locator("#run").click()                                                     # the site is allowed now: only the risky one asks
        panel.wait_for_selector("#confirmh:has-text('hard to undo')")
        panel.locator("#confirmyes").click()
        assert banner(panel) == "passed"
        assert "Thanks, Asha" in page.locator("#out").inner_text()
    finally:
        storage(browser, askBeforeRunning=False, localAllowed=True, allowedSites=[])


# ---------------------------------------------------------------------------------------------- fixing a failed step
def test_a_failed_step_is_fixed_by_picking_the_right_element_and_run_again_from_there(browser, sites, rhub):
    base, _ = rhub
    panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
    panel.locator("#text").fill('Type "Asha" into the Zzzq field\nClick Place order\nVerify page contains "Thanks, Asha"')
    panel.locator("#run").click()
    assert banner(panel) == "failed"
    failed = panel.locator("#steps li.failed").first
    assert "Could not identify" in failed.locator(".err").inner_text()
    assert failed.locator("img").is_visible()                                              # what the page looked like is right there
    failed.get_by_role("button", name="Pick the right element").click()
    page.bring_to_front()
    page.click("#name")                                                                   # the person clicks the right one on the page
    panel.locator("#steps li.failed .tools [data-act=rerun]").wait_for(state="visible", timeout=20000)
    assert "Now uses" in panel.locator("#steps li.failed").inner_text()
    panel.locator("#steps li.failed [data-act=rerun]").click()
    assert banner(panel) == "passed"
    assert "Thanks" in page.locator("#out").inner_text()
    row = next(r for r in runs(base) if "(fixed)" in r["test_name"] or r["status"] == "passed")
    assert row["status"] == "passed"


# ---------------------------------------------------------------------------------------------- editing a recording
def test_a_recording_can_be_edited_reordered_parameterised_and_checked_before_saving(browser, sites, rhub):
    base, _ = rhub
    srv = _Page(ORDER_PAGE)
    srv.start()
    panel, page = record(browser, srv.url)
    steps = panel.locator("#recsteps li")
    n = steps.count()
    first_type = panel.locator("#recsteps li", has_text='type "Asha"').first
    first_type.get_by_role("button", name="Edit step").click()
    panel.locator("#ev").fill("Bea")
    panel.locator("form[data-form=edit] button[type=submit]").click()
    assert 'type "Bea"' in panel.locator("#recsteps").inner_text()
    panel.locator("#recsteps li", has_text='type "Bea"').first.get_by_role("button", name="data variable").click()
    panel.locator("#vn").fill("visitor")
    panel.locator("form[data-form=var] button[type=submit]").click()
    assert "${visitor}" in panel.locator("#recsteps").inner_text()
    last = panel.locator("#recsteps li").last
    last.get_by_role("button", name="Add a check").click()
    panel.locator("#ct").fill("Order placed")
    panel.locator("form[data-form=check] button[type=submit]").click()
    assert panel.locator("#recsteps li").count() == n + 1
    panel.locator("#recsteps li").nth(1).get_by_role("button", name="Move step 2 down").click()      # reorder, by button (keyboard friendly)
    panel.locator("#recsteps li").last.get_by_role("button", name="Remove step").click()              # remove the check again
    assert panel.locator("#recsteps li").count() == n
    panel.locator("#recname").fill("Edited order")
    panel.locator("#recsave").click()
    end = time.time() + 20
    while time.time() < end and not any(x["name"] == "Edited-order" for x in api(base, "/api/library")):
        time.sleep(0.3)
    saved = api(base, "/api/library/Edited-order")
    assert saved["data"].get("visitor") == "Bea"
    assert any(s["value"] == "${visitor}" for s in saved["steps"])


def test_recording_can_be_paused_and_the_last_thing_undone(browser, sites):
    panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
    panel.locator("#mrec").click()
    panel.locator("#recstart").click()
    panel.wait_for_selector("#recstop:not([hidden])")
    page.bring_to_front()
    page.get_by_label("Name").click(); page.keyboard.type("Asha"); page.get_by_label("Quantity").focus()
    time.sleep(1.2)
    n0 = panel.locator("#recsteps li").count()
    panel.locator("#recpause").click()
    assert panel.locator("#recpause").get_attribute("aria-pressed") == "true"
    page.get_by_role("button", name="Place order").click()                                # while paused: not recorded
    time.sleep(1)
    panel.locator("#recpause").click()
    time.sleep(1)
    assert panel.locator("#recsteps li").count() == n0
    page.get_by_label("Send me the newsletter").check()
    time.sleep(1.2)
    n1 = panel.locator("#recsteps li").count()
    assert n1 == n0 + 1
    panel.locator("#recundo").click()
    time.sleep(1.2)
    assert panel.locator("#recsteps li").count() == n0
    panel.locator("#recstop").click()


# ---------------------------------------------------------------------------------------------- run controls, names, results
def test_run_step_by_step_pause_resume_and_stop(browser, sites):
    panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
    panel.locator("#text").fill(ORDER)
    panel.locator("#stepmode").check()
    panel.locator("#run").click()
    panel.locator("#next").wait_for(state="visible", timeout=30000)                      # held before the first step
    assert page.locator("#name").input_value() == ""
    panel.locator("#next").click()                                                       # step 1 opens the page
    panel.wait_for_selector("#steps li.passed", timeout=30000)
    panel.locator("#next").click()                                                       # step 2 types the name
    panel.wait_for_function if False else None
    end = time.time() + 30
    while time.time() < end and page.locator("#name").input_value() != "Asha":
        time.sleep(0.2)
    time.sleep(1)
    assert page.locator("#name").input_value() == "Asha" and page.locator("#out").inner_text() == ""      # one step at a time, held between
    panel.locator("#pause").click()                                                       # resume: the rest runs
    assert banner(panel) == "passed"
    assert "Thanks, Asha" in page.locator("#out").inner_text()


def test_a_run_has_a_name_and_its_results_are_in_the_panel(browser, sites, rhub):
    base, _ = rhub
    panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
    panel.locator("#text").fill(ORDER)
    panel.locator("#runname").fill("My checkout run")
    panel.locator("#run").click()
    assert banner(panel) == "passed"
    panel.wait_for_selector("#results:not([hidden])", timeout=60000)
    assert "/#/runs/" in panel.locator("#reportlink").get_attribute("href")
    end = time.time() + 40
    while time.time() < end and not any(r["test_name"].startswith("My checkout run") for r in runs(base)):
        time.sleep(0.5)
    assert any(r["test_name"].startswith("My checkout run") for r in runs(base))          # the name given is the name on the hub
    panel.locator("#watch").click()
    panel.wait_for_selector("#video:not([hidden])", timeout=30000)
    assert panel.locator("#video").get_attribute("src").startswith("blob:")
    panel.locator("#savescript").click()
    end = time.time() + 20
    while time.time() < end and not any("My-checkout-run" in x["name"] for x in api(base, "/api/library")):
        time.sleep(0.4)
    assert any("My-checkout-run" in x["name"] for x in api(base, "/api/library"))


def test_history_can_be_searched(browser, rhub):
    panel, _ = open_panel(browser)
    panel.locator("#hubd summary").click()
    panel.wait_for_selector("#hublists .it", timeout=15000)
    total = panel.locator("#hublists .it").count()
    panel.locator("#hubsearch").fill("My checkout")
    shown = panel.locator("#hublists .it").count()
    assert 0 < shown <= total
    panel.locator("#hubsearch").fill("zzzz-no-such-thing")
    assert panel.locator("#hublists .it").count() == 0 and "matches" in panel.locator("#hublists").inner_text()


def test_a_note_about_the_wording_takes_you_to_that_wording(browser, sites):
    panel, _ = open_panel(browser, sites["site"] + "/extension/order.html")
    panel.locator("#text").fill(ORDER)
    panel.locator("#preview").click()
    panel.wait_for_selector("#notes:not([hidden])", timeout=30000)
    link = panel.locator("#notes .noteline").first
    link.click()
    chosen = panel.evaluate("() => { const t = document.getElementById('text'); return t.value.substring(t.selectionStart, t.selectionEnd); }")
    assert chosen and chosen in ORDER


def test_closing_chromes_debugging_bar_stops_the_run_and_says_why(browser, sites):
    storage(browser, testHooks=True)
    try:
        panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
        panel.locator("#text").fill(ORDER)
        panel.locator("#stepmode").check()
        panel.locator("#run").click()
        panel.locator("#next").wait_for(state="visible", timeout=30000)
        tab = panel.evaluate("async () => (await new Promise((r) => chrome.runtime.sendMessage({ type: 'target' }, r))).tab.id")
        panel.evaluate("async (id) => await new Promise((r) => chrome.runtime.sendMessage({ type: 'test-detach', tabId: id }, r))", tab)
        panel.wait_for_selector("#msg:not([hidden])", timeout=20000)
        assert "debugging this browser" in panel.locator("#msg").inner_text()
        assert banner(panel, timeout=60) in ("cancelled", "failed")
    finally:
        storage(browser, testHooks=False)


# ---------------------------------------------------------------------------------------------- accessibility
def test_the_panel_is_usable_with_a_keyboard_and_a_screen_reader(browser, sites):
    panel, page = open_panel(browser, sites["site"] + "/extension/order.html")
    # landmarks and a live region
    assert panel.locator("main").count() == 1 and panel.locator("header h1").count() == 1
    assert panel.locator("#live").get_attribute("role") == "status" and panel.locator("#live").get_attribute("aria-live") == "polite"
    # every control a person can use has a name
    nameless = panel.evaluate("""() => [...document.querySelectorAll('button, input, textarea, select, a[href], summary')].filter((e) => !e.closest('[hidden]') && e.type !== 'hidden')
        .filter((e) => { const name = (e.getAttribute('aria-label') || e.title || e.textContent || e.placeholder || '').trim() || (e.labels && e.labels.length && e.labels[0].textContent.trim());
                        return !name; }).map((e) => e.id || e.className || e.tagName)""")
    assert nameless == [], nameless
    # dialogs say so
    for dlg in ("zoom", "confirm"):
        assert panel.locator(f"#{dlg}").get_attribute("role") == "dialog" and panel.locator(f"#{dlg}").get_attribute("aria-modal") == "true"
    assert panel.locator("#mrun").get_attribute("role") == "tab" and panel.locator("#mrun").get_attribute("aria-selected") == "true"
    # a visible focus ring (keyboard focus)
    panel.keyboard.press("Tab")
    panel.locator("#preview").focus()
    assert panel.evaluate("() => getComputedStyle(document.getElementById('preview')).outlineStyle") != "none"
    # the shortcut runs it
    panel.locator("#text").fill(ORDER)
    panel.locator("#text").press("Control+Enter")
    assert banner(panel) == "passed"
    # announced: the result reaches the live region
    panel.wait_for_function("() => /passed/i.test(document.getElementById('live').textContent)", timeout=5000)


def test_dark_theme_and_reduced_motion_are_respected(browser):
    panel, _ = open_panel(browser)
    lum = lambda css: panel.evaluate("""(prop) => { const c = getComputedStyle(document.body)[prop].match(/\\d+(\\.\\d+)?/g).map(Number); return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255; }""", css)
    panel.emulate_media(color_scheme="light")
    assert lum("backgroundColor") > 0.85 and lum("color") < 0.3
    panel.emulate_media(color_scheme="dark")
    assert lum("backgroundColor") < 0.2 and lum("color") > 0.7
    panel.emulate_media(reduced_motion="reduce")
    anim = panel.evaluate("""() => { const li = document.createElement('li'); li.className = 'step running'; li.innerHTML = '<span class="ic">1</span>'; document.getElementById('steps').append(li);
        const a = getComputedStyle(li.querySelector('.ic')).animationName; li.remove(); return a; }""")
    assert anim == "none"
