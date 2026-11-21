"""'Check replay': right after recording, replay the recording on the same tab with the server's engine and mark every step that does not
reproduce, before anything is saved."""
import time

from .test_extension import close_extra, ready, sites  # noqa: F401
from .test_extension_server import _Page, api, browser, rhub, runs  # noqa: F401
from .test_extension import ROOT

ORDER = (ROOT / "examples" / "extension" / "order.html").read_text()


def record(browser, url):
    ctx, ext = browser
    close_extra(browser)
    page = ctx.new_page()
    page.goto(url)
    page.bring_to_front()
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#mrec").click()
    panel.locator("#recstart").click()
    panel.wait_for_selector("#recstop:not([hidden])")
    page.bring_to_front()
    page.get_by_label("Name").click(); page.keyboard.type("Asha")
    page.get_by_role("button", name="Place order").click()
    page.wait_for_selector("#out:has-text('Thanks, Asha')")
    time.sleep(1)
    panel.locator("#recstop").click()
    panel.wait_for_selector("#recout:not([hidden])", timeout=30000)
    return panel, page


def test_check_replay_confirms_a_recording_that_reproduces(browser, rhub):
    srv = _Page(ORDER)
    srv.start()
    panel, page = record(browser, srv.url)
    page.bring_to_front()
    panel.locator("#reccheck").click()
    panel.locator("#checkres", has_text="reproduce").wait_for(timeout=90000)
    text = panel.locator("#checkres").inner_text()
    assert "All" in text and "reproduced" in text, text
    assert panel.locator("#recsteps li.bad").count() == 0 and panel.locator("#recsteps li.ok").count() >= 3


def test_check_replay_flags_the_step_that_does_not_reproduce(browser, rhub):
    srv = _Page(ORDER)
    srv.start()
    panel, page = record(browser, srv.url)
    srv.html = ORDER.replace('<label for="name">Name</label><input id="name">', "")          # the field the recording typed into is gone
    page.bring_to_front()
    panel.locator("#reccheck").click()
    panel.locator("#checkres", has_text="did not reproduce").wait_for(timeout=120000)
    bad = panel.locator("#recsteps li.bad")
    assert bad.count() >= 1
    assert "Name" in bad.first.inner_text()                                                    # the step is marked, with why
    assert "Could not identify" in panel.locator("#recsteps").inner_text() or "not found" in panel.locator("#recsteps").inner_text().lower()
