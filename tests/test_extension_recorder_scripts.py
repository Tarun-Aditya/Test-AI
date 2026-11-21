"""The recorder captures a whole real session and the scripts made from it run on their own.

A person's session on the server's playground page (every kind of input, upload, a hover menu, a custom dropdown, native dialogs, a
contenteditable editor, drag and drop, nested shadow roots and nested iframes, scrolling) is recorded in a real Chromium with the
extension, saved to the hub, run by the app, and then the generated Playwright and Selenium scripts are run as plain scripts, copied
elsewhere, without this tool on their path.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .test_extension import close_extra, ready, sites  # noqa: F401
from .test_extension_server import APP_CFG, api, browser, rhub, runs, standalone, wait_run  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def human_session(page):
    """What a person does on the playground, with real mouse and keyboard input."""
    page.on("dialog", lambda d: d.dismiss() if d.type == "confirm" else d.accept())
    page.get_by_label("First name").click(); page.keyboard.type("Asha")
    page.get_by_label("Last name").click(); page.keyboard.type("Rao")
    page.get_by_placeholder("you@example.com").click(); page.keyboard.type("asha@example.com")
    page.locator("input[type=password]").click(); page.keyboard.type("S3cret!!")
    page.get_by_label("Quantity").click(); page.keyboard.type("3")
    page.get_by_label("Date of birth").click(); page.keyboard.type("05171990")
    page.get_by_label("Volume").focus()
    for _ in range(5):
        page.keyboard.press("ArrowRight")                                    # a slider moved with the keyboard
    page.get_by_label("Bio").click(); page.keyboard.type("Loves testing")
    page.get_by_label("Country").focus(); page.keyboard.press("I")           # picks India
    page.get_by_role("option", name="English").click()
    page.get_by_role("option", name="Telugu").click(modifiers=["Control"])   # a multiple selection
    page.get_by_label("Pro").click()
    page.get_by_label("I agree to the terms").check()
    page.get_by_label("Send me the newsletter").uncheck()
    page.get_by_label("Resume").set_input_files(str(ROOT / "examples" / "playground_data.json"))
    page.get_by_role("button", name="Submit form").click()
    # widgets
    page.get_by_role("combobox", name="Size").click()
    page.get_by_role("option", name="Large").click()
    page.get_by_role("button", name="Open row").dblclick()
    page.get_by_role("button", name="File actions").click(button="right")
    page.get_by_role("button", name="Delete item").click()                   # a confirm, answered "cancel"
    page.get_by_role("button", name="Show alert").click()                    # an alert, accepted
    page.locator("[aria-label='Notes editor']").click(); page.keyboard.type("Remember the milk")
    page.get_by_role("link", name="Settings").hover(); time.sleep(0.9)       # a menu that opens on hover
    page.get_by_role("link", name="Profile settings").click()
    page.locator("#card-a").drag_to(page.get_by_label("Done column"))        # drag and drop
    page.get_by_role("button", name="Load more").click()
    page.get_by_role("button", name="View").nth(1).click()
    # nested shadow roots
    page.get_by_label("Display name").click(); page.keyboard.type("Ravi")
    page.get_by_role("button", name="Update profile").click()
    page.get_by_placeholder("City").click(); page.keyboard.type("Hyderabad")
    page.get_by_role("button", name="Save address").click()
    # iframes, one inside another
    pay = page.frame_locator("#pay-frame")
    pay.get_by_label("Card number").click(); page.keyboard.type("4111111111114242")
    pay.get_by_role("button", name="Pay now").click()
    pay.frame_locator("iframe").get_by_role("button", name="Apply coupon").click()
    page.mouse.wheel(0, 1500); time.sleep(1.2)


def test_a_whole_session_is_recorded_and_the_scripts_made_from_it_run_on_their_own(browser, sites, rhub, tmp_path):
    base, arts = rhub
    ctx, ext = browser
    close_extra(browser)
    page = ctx.new_page()
    page.goto(sites["site"] + "/playground.html")
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
    shown = "\n".join(li.inner_text() for li in panel.locator("#recsteps li").all())

    panel.locator("#recname").fill("Playground session")
    panel.locator("#recsave").click()
    end = time.time() + 20
    while time.time() < end and not any(x["name"] == "Playground-session" for x in api(base, "/api/library")):
        time.sleep(0.3)
    saved = api(base, "/api/library/Playground-session")
    steps = saved["steps"]
    kinds = [s["action"] for s in steps]
    # everything the person did was captured
    for a in ("type", "select", "check", "uncheck", "upload", "double_click", "right_click", "hover", "drag", "dialog", "assert_dialog", "scroll", "click"):
        assert a in kinds, (a, kinds)
    assert kinds.count("type") >= 10 and kinds.count("select") >= 2
    typed = {s["target"]: s["value"] for s in steps if s["action"] == "type"}
    assert any(v == "Asha" for v in typed.values()) and any(v == "Hyderabad" for v in typed.values()) and any(v == "Remember the milk" for v in typed.values())
    assert any(v == "${password}" for v in typed.values()) and "S3cret!!" not in json.dumps(saved) and "S3cret!!" not in shown       # the secret was lifted out
    assert any(s["action"] == "type" and "4242" in (s["value"] or "") and len(s["locator"]["frames"]) == 1 for s in steps)        # an iframe
    assert any(s["action"] == "click" and "coupon" in (s["target"] or "").lower() and len(s["locator"]["frames"]) == 2 for s in steps)   # an iframe in an iframe
    assert any(s["locator"] and len(s["locator"]["shadow"]) == 2 for s in steps if s.get("locator"))                                 # nested shadow roots
    assert [s for s in steps if s["action"] == "dialog"][0]["value"] == "dismiss"
    assert saved["data"]["upload_file"] and saved["sensitive"] == ["password"]

    # the app runs it (the person supplies the file path and the secret, as for any saved script)
    data = {"upload_file": str(ROOT / "examples" / "playground_data.json"), "password": "S3cret!!"}
    r = api(base, "/api/runs", {"library": "Playground-session", "data": data, "sensitive": ["password"], "config": {**APP_CFG, "timeout_ms": 8000}, "agent": "local"})
    row = wait_run(base, r["run_ids"][0], timeout=300)
    detail = api(base, f"/api/runs/{row['run_id']}")
    assert row["status"] == "passed", [(s["raw"], s["error"]) for s in detail["steps"] if s["error"]]

    # the scripts made from it run on their own
    run_dir = arts / row["run_id"]
    for script in ("test_playwright.py", "test_selenium.py"):
        res, out = standalone(tmp_path, run_dir, script, {"AIAUTO_SECRET_PASSWORD": "S3cret!!"})
        assert res.returncode == 0 and "PASSED" in res.stdout, (script, (res.stdout + res.stderr)[-2500:])
        summary = json.loads((out / "summary.json").read_text())
        assert summary["status"] == "passed" and all(st["status"] == "passed" for st in summary["steps"])
