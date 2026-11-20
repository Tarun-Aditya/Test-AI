"""Recorded flows -> steps: the compiler for the extension's events, and the Chrome DevTools Recorder import."""
import json

from aiauto.models import Action
from aiauto.recorder import compile_events, from_chrome_recording


def el(tag, name, loc, **kw):
    return {"tag": tag, "name": name, "label": name if tag in ("input", "select") else "", "text": name if tag in ("a", "button") else "",
            "role": kw.pop("role", {"a": "link", "button": "button"}.get(tag, "")), "marker": kw.pop("marker", f"m:{name}"),
            "locators": [loc], "frames": [], "shadow": [], **kw}


NAME = el("input", "Full name", {"strategy": "label", "value": "Full name"}, type="text")
PASS = el("input", "Password", {"strategy": "label", "value": "Password"}, type="password")
OPEN = el("a", "Open the order form", {"strategy": "role", "value": "Open the order form", "role": "link"})
GO = el("button", "Place order", {"strategy": "role", "value": "Place order", "role": "button"})
QTY = el("select", "Quantity", {"strategy": "label", "value": "Quantity"})


def test_events_become_steps_with_stored_locators_and_a_description():
    t, notes = compile_events([
        {"type": "navigate", "url": "http://x/a", "tab": 1, "t": 0},
        {"type": "click", "el": NAME, "tab": 1, "t": 1},                      # clicking into the field before typing: not a step
        {"type": "input", "el": NAME, "value": "Asha", "tab": 1, "t": 2},
        {"type": "select", "el": QTY, "value": "2", "tab": 1, "t": 3},
        {"type": "click", "el": GO, "tab": 1, "t": 4},
        {"type": "assert_text", "value": "Thanks, Asha", "tab": 1, "t": 5},
    ])
    assert [s.action for s in t.steps] == [Action.NAVIGATE, Action.TYPE, Action.SELECT, Action.CLICK, Action.ASSERT_TEXT]
    typ = t.steps[1]
    assert typ.target == "Full name" and typ.value == "Asha" and typ.source == "recorded"
    assert typ.locator.strategy == "label" and typ.locator.value == "Full name"
    assert t.steps[3].target == "Place order button" and t.steps[3].locator.role == "button"
    assert t.base_url == "http://x/a"
    assert [s.raw for s in t.steps][1] == 'type "Asha" into Full name'


def test_a_password_is_lifted_into_data_and_never_left_in_a_step():
    t, _ = compile_events([{"type": "input", "el": PASS, "value": "hunter2!", "secret": True, "tab": 1, "t": 1}])
    assert t.sensitive == ["password"] and t.data["password"] == "hunter2!"
    assert t.steps[0].value == "${password}" and "hunter2" not in t.steps[0].raw


def test_the_page_load_a_click_causes_is_not_a_step_but_a_typed_address_is():
    t, _ = compile_events([
        {"type": "navigate", "url": "http://x/a", "tab": 1, "t": 0},
        {"type": "click", "el": GO, "tab": 1, "t": 10},
        {"type": "navigate", "url": "http://x/b", "tab": 1, "t": 11},          # the click's effect
        {"type": "navigate", "url": "http://x/c", "tab": 1, "t": 60},          # the person typed an address much later... it is a page change with no cause
    ])
    assert [s.value for s in t.steps if s.action == Action.NAVIGATE] == ["http://x/a", "http://x/c"]


def test_a_new_tab_is_followed_and_going_back_to_the_first_is_a_step():
    t, _ = compile_events([
        {"type": "navigate", "url": "http://x/a", "tab": 1, "t": 0},
        {"type": "click", "el": OPEN, "tab": 1, "t": 1},
        {"type": "navigate", "url": "http://x/order", "tab": 2, "t": 1.5},     # the tab the click opened: followed on replay
        {"type": "input", "el": NAME, "value": "Asha", "tab": 2, "t": 3},
        {"type": "click", "el": GO, "tab": 1, "t": 4},                         # the person went back to tab 1
    ])
    acts = [(s.action, s.value) for s in t.steps]
    assert (Action.SWITCH_TAB, "0") in acts and not any(a == Action.NAVIGATE and v == "http://x/order" for a, v in acts)


def test_a_double_click_is_one_step_not_three_and_enter_flushing_typing_does_not_duplicate_it():
    t, _ = compile_events([
        {"type": "click", "el": GO, "tab": 1, "t": 1}, {"type": "click", "el": GO, "tab": 1, "t": 1.1},
        {"type": "dblclick", "el": GO, "tab": 1, "t": 1.2},
        {"type": "input", "el": NAME, "value": "Asha", "tab": 1, "t": 2},
        {"type": "key", "key": "Enter", "el": NAME, "tab": 1, "t": 2.1},
        {"type": "input", "el": NAME, "value": "Asha", "tab": 1, "t": 2.2},
    ])
    assert [s.action for s in t.steps] == [Action.DOUBLE_CLICK, Action.TYPE, Action.PRESS]


CHROME = {"title": "Sign up flow", "steps": [
    {"type": "setViewport", "width": 1000, "height": 800},
    {"type": "navigate", "url": "https://shop.example/signup", "assertedEvents": [{"type": "navigation", "url": "https://shop.example/signup"}]},
    {"type": "click", "selectors": [["aria/Email address"], ["#email"]], "offsetX": 5, "offsetY": 5},
    {"type": "change", "value": "me@example.com", "selectors": [["aria/Email address"], ["#email"]]},
    {"type": "change", "value": "s3cret-pass", "selectors": [["aria/Password"], ["#pw"]]},
    {"type": "keyDown", "key": "Enter"}, {"type": "keyUp", "key": "Enter"},
    {"type": "click", "selectors": [["aria/Create account[role=\"button\"]"], ["button.primary"], ["xpath///button[1]"]]},
    {"type": "waitForElement", "selectors": [["text/Welcome"]]},
    {"type": "scroll", "x": 0, "y": 400},
]}


def test_a_chrome_recorder_export_becomes_the_same_steps():
    t, notes = from_chrome_recording(json.dumps(CHROME))
    assert t.name == "Sign up flow" and t.base_url == "https://shop.example/signup"
    assert [s.action for s in t.steps] == [Action.NAVIGATE, Action.CLICK, Action.TYPE, Action.TYPE, Action.PRESS, Action.CLICK, Action.WAIT]
    btn = t.steps[5]
    assert btn.target == "Create account button" and btn.locator.strategy == "role" and btn.locator.role == "button"
    assert [l.strategy for l in btn.fallbacks][:2] == ["css", "xpath"]
    assert t.sensitive == ["password"] and t.data["password"] == "s3cret-pass" and t.steps[3].value == "${password}"
    assert any("scroll" in n for n in notes)


def test_something_that_is_not_a_recording_is_refused_clearly():
    import pytest
    with pytest.raises(ValueError, match="Recorder"):
        from_chrome_recording({"nope": 1})
