"""Complex DOM through the extension: shadow roots three deep, a shadow root inside an iframe, an iframe inside a shadow root, the same
label in two shadow roots, elements that re-render (new nodes, new ids) on every click, elements that appear late, identical rows.
Recorded as a person's session, saved, replayed by the app, and the generated scripts run on their own (Playwright and Selenium); and the
same page driven by a natural-language scenario from the panel."""
import json
import time

from .test_extension import close_extra, ready, sites  # noqa: F401
from .test_extension_server import (APP_CFG, _assert_scripts_run, _run_from_panel, api, browser, new_extension_run, rhub, runs, saved, wait_run)  # noqa: F401


def human_session(page):
    page.get_by_label("Deepest note").click(); page.keyboard.type("level three")
    page.get_by_role("button", name="Confirm deepest").click()
    frame = page.frame_locator("iframe[title='Frame with shadow']")
    frame.get_by_label("Coupon code").click(); page.keyboard.type("SAVE10")
    frame.get_by_role("button", name="Redeem").click()
    inner = page.frame_locator("iframe[title='Frame in shadow']")
    inner.get_by_label("Gift message").click(); page.keyboard.type("Happy birthday")
    inner.get_by_role("button", name="Wrap gift").click()
    for _ in range(3):                                                        # the button is thrown away and rebuilt after every click
        page.get_by_role("button", name="Add to basket").click()
    page.get_by_role("button", name="Appears late").click()                   # shows up after ~2 s
    page.get_by_role("button", name="Load report").click()
    page.get_by_role("button", name="Edit").nth(1).click()                    # identical rows: the second one
    cards = page.locator("address-card")
    cards.nth(1).get_by_label("Name").click(); page.keyboard.type("Ravi")      # the same label in two shadow roots: the shipping one
    cards.nth(1).get_by_role("button", name="Save").click()
    cards.nth(0).get_by_label("Name").click(); page.keyboard.type("Asha")
    cards.nth(0).get_by_role("button", name="Save").click()
    time.sleep(0.5)


def test_a_session_on_a_complex_page_records_replays_and_its_scripts_run_on_their_own(browser, sites, rhub, tmp_path):
    base, arts = rhub
    ctx, ext = browser
    close_extra(browser)
    page = ctx.new_page()
    page.goto(sites["site"] + "/extension/complex.html")
    page.bring_to_front()
    panel = ctx.new_page()
    panel.goto(f"chrome-extension://{ext}/panel.html")
    ready(panel)
    panel.locator("#mrec").click()
    panel.locator("#recstart").click()
    panel.wait_for_selector("#recstop:not([hidden])")
    page.bring_to_front()
    human_session(page)
    time.sleep(1)
    panel.locator("#recstop").click()
    panel.wait_for_selector("#recout:not([hidden])", timeout=30000)
    panel.locator("#recname").fill("Complex page")
    panel.locator("#recsave").click()
    end = time.time() + 20
    while time.time() < end and not any(x["name"] == "Complex-page" for x in api(base, "/api/library")):
        time.sleep(0.3)
    steps = api(base, "/api/library/Complex-page")["steps"]
    loc = lambda s: s.get("locator") or {}
    assert any(len(loc(s).get("shadow", [])) == 3 for s in steps), "three shadow roots deep"
    assert any(len(loc(s).get("frames", [])) == 1 and len(loc(s).get("shadow", [])) >= 1 for s in steps), "a shadow root inside an iframe"
    assert any(s["action"] == "type" and s["value"] == "Happy birthday" and len(loc(s).get("frames", [])) == 1 for s in steps), "an iframe inside a shadow root"
    assert sum(1 for s in steps if s["action"] == "click" and "basket" in (s["target"] or "").lower()) == 3
    names = [s["value"] for s in steps if s["action"] == "type" and s["value"] in ("Ravi", "Asha")]
    assert names == ["Ravi", "Asha"]                                            # the right "Name" field each time, in the order done

    r = api(base, "/api/runs", {"library": "Complex-page", "config": {**APP_CFG, "timeout_ms": 8000}, "agent": "local"})
    row = wait_run(base, r["run_ids"][0], timeout=240)
    detail = api(base, f"/api/runs/{row['run_id']}")
    assert row["status"] == "passed", [(s["raw"], s["error"]) for s in detail["steps"] if s["error"]]
    log = "".join(s.get("raw", "") for s in detail["steps"])
    (tmp_path / "rec").mkdir()
    _assert_scripts_run(tmp_path / "rec", arts / row["run_id"])


NL = """Type "level three" into Deepest note
Click Confirm deepest
Verify page contains "Deepest confirmed: level three"
Type "SAVE10" into Coupon code
Click Redeem
Verify page contains "Redeemed SAVE10"
Type "Happy birthday" into Gift message
Click Wrap gift
Verify page contains "Wrapped: Happy birthday"
Click Add to basket
Click Add to basket
Verify page contains "Basket is 2"
Click Appears late
Verify page contains "Late button used"
Click Load report
Click the second Edit button
Verify page contains "Edit order 102"
Type "Ravi" into the Name in the Shipping address
Click the Save button in the Shipping address
Verify page contains "Shipping saved for Ravi"
"""


def test_the_same_page_from_a_natural_language_scenario(browser, sites, rhub, tmp_path):
    base, arts = rhub
    known = {r["run_id"] for r in runs(base)}
    status, panel, page = _run_from_panel(browser, sites["site"] + "/extension/complex.html", NL)
    steps = [(li.get_attribute("class").split()[-1], li.locator(".tx").inner_text(), li.locator(".err").inner_text() if li.locator(".err").count() else "")
             for li in panel.locator("#steps li").all()]
    assert status == "passed", [s for s in steps if s[0] != "passed"][:3]
    saved(panel)
    rid = new_extension_run(base, known)
    assert wait_run(base, rid)["status"] == "passed"
    time.sleep(1)
    (tmp_path / "nl").mkdir()
    _assert_scripts_run(tmp_path / "nl", arts / rid)
