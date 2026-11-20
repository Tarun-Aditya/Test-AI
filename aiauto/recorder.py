"""Recorded flows -> the same steps a typed scenario produces.

Two sources:
  * the Chrome extension's recorder: raw events (click, typing, select, key, new tab ...) each carrying a fingerprint of the
    element (what the engines' DESCRIBE_EL_JS returns: role, name, label, text, locators ...);
  * Chrome DevTools' Recorder export (the JSON "Puppeteer Replay" format).

Both come out as TestCase steps with a stored locator (and fallbacks) and an English `target`, so a recording replays like any
saved script (stored locator first, the description as the self-healing fallback), exports to Playwright / Selenium code and
reads like a plain-English test. No vision involved: everything comes from the page's own structure.
"""
from __future__ import annotations

import re
from typing import Any

from .locators import to_locators
from .models import Action, Step, TestCase

ENTER_LIKE = {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Delete", "Backspace", "Space", " "}
SUBMITTING = 5.0      # seconds: a page load this soon after a click / Enter is that action's effect, not a typed address


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _kind(el: dict) -> str:
    tag, typ, role = el.get("tag", ""), (el.get("type") or "").lower(), (el.get("role") or "").lower()
    if tag == "select" or role in ("combobox", "listbox"):
        return "dropdown"
    if tag == "textarea" or (tag == "input" and typ not in ("submit", "button", "reset", "checkbox", "radio", "image")) or role == "textbox":
        return "field"
    if typ == "checkbox" or role == "checkbox":
        return "checkbox"
    if typ == "radio" or role == "radio":
        return "radio button"
    if tag == "a" or role == "link":
        return "link"
    if tag == "button" or typ in ("submit", "button", "reset") or role == "button":
        return "button"
    return ""


def describe_target(el: dict) -> str:
    """How a person would name the element: its visible name, then the kind of control ("Place order button")."""
    kind = _kind(el)
    for k in ("name", "label", "aria_label", "text", "placeholder", "value", "title", "alt", "nameattr", "id"):
        v = _clean(el.get(k))
        if v:
            v = v[:60]
            if kind and not v.lower().endswith(kind.split()[0]) and kind != "field":
                return f"{v} {kind}"
            return v if kind != "field" else f"{v}"
    return kind or el.get("tag") or "element"


def _locators(el: dict) -> tuple[Any, list[Any]]:
    locs = to_locators(el) if el.get("locators") else []
    return (locs[0] if locs else None), locs[1:6]


def _same(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    if a.get("marker") and a.get("marker") == b.get("marker"):
        return True
    la, lb = a.get("locators") or [], b.get("locators") or []
    return bool(la and lb and la[0] == lb[0] and a.get("frames") == b.get("frames"))


def compile_events(events: list[dict], data: dict | None = None, sensitive: list[str] | None = None,
                   name: str = "Recorded flow", base_url: str | None = None) -> tuple[TestCase, list[str]]:
    """Raw recorder events -> a TestCase. Returns (test, notes)."""
    data = dict(data or {})
    sens = list(sensitive or [])
    notes: list[str] = []
    steps: list[Step] = []
    seen_tabs: list[Any] = []
    cur_tab: Any = None
    last_t = 0.0
    last_acted = False
    last_val: dict[str, str] = {}

    def add(action: Action, target: str | None, value: str | None, raw: str, el: dict | None = None, **kw) -> Step:
        loc, fb = _locators(el) if el else (None, [])
        st = Step(action=action, target=target, value=value, raw=raw, source="recorded", locator=loc, fallbacks=fb, **kw)
        steps.append(st)
        return st

    def secret_key() -> str:
        k, n = "password", 1
        while k in data:
            n += 1
            k = f"password{n}"
        return k

    evs = [e for e in events if e and e.get("type")]
    for n, e in enumerate(evs):
        t, el, tab = e["type"], e.get("el") or {}, e.get("tab")
        ts = float(e.get("t") or 0)
        if tab is not None and tab != cur_tab and t != "navigate":
            if cur_tab is not None and tab in seen_tabs:
                add(Action.SWITCH_TAB, None, str(seen_tabs.index(tab)), f"switch to tab {seen_tabs.index(tab) + 1}")
            cur_tab = tab                         # a tab the last action opened is followed automatically on replay
        if tab is not None and tab not in seen_tabs:
            seen_tabs.append(tab)
        nxt = evs[n + 1] if n + 1 < len(evs) else None

        if t == "navigate":
            url = e.get("url") or ""
            if tab is not None and tab != cur_tab and cur_tab is not None:
                cur_tab = tab                     # the navigation that opened a new tab: not a step of its own
                continue
            cur_tab = tab if tab is not None else cur_tab
            if steps and last_acted and ts and ts - last_t < SUBMITTING and (e.get("cause") or "page") == "page":
                continue                          # the page load a click / Enter caused
            if steps and steps[-1].action == Action.NAVIGATE and steps[-1].value == url:
                continue
            if url and not url.startswith(("about:", "chrome:", "edge:")):
                add(Action.NAVIGATE, None, url, f"open {url}")
                if not base_url:
                    base_url = url
            last_acted = False
        elif t in ("click", "dblclick", "rightclick"):
            if not el:
                continue
            kind = _kind(el)
            if nxt and nxt["type"] == "input" and _same(el, nxt.get("el")):
                continue                          # clicking into a field before typing in it
            if (el.get("type") or "").lower() in ("checkbox", "radio") or el.get("role") in ("checkbox", "radio", "switch"):
                continue                          # the "check" event that follows says what it ended up as
            if el.get("tag") == "select" or el.get("tag") == "option":
                continue
            tg = describe_target(el)
            if t == "dblclick":
                for _ in range(2):                # the two clicks a double-click is made of
                    if steps and steps[-1].action == Action.CLICK and steps[-1].target == tg:
                        steps.pop()
            verb, act = {"click": ("click", Action.CLICK), "dblclick": ("double-click", Action.DOUBLE_CLICK), "rightclick": ("right-click", Action.RIGHT_CLICK)}[t]
            add(act, tg, None, f"{verb} {tg}", el)
            last_acted, last_t = True, ts
        elif t == "input":
            if not el:
                continue
            val = str(e.get("value", ""))
            tg = describe_target(el)
            mk = str(el.get("marker") or (el.get("locators") or [{}])[0])
            if last_val.get(mk) == val:
                continue                          # the same text reported twice (Enter flushes it, then `change` fires)
            last_val[mk] = val
            if (el.get("type") or "").lower() == "password" or e.get("secret"):
                k = secret_key()
                data[k] = val
                sens.append(k)
                val = "${" + k + "}"
            add(Action.TYPE, tg, val, f'type "{val}" into {tg}', el)
            last_acted = False
        elif t == "select":
            tg = describe_target(el)
            while steps and steps[-1].action == Action.PRESS and steps[-1].value.startswith("Arrow") and steps[-1].target == tg:
                steps.pop()                       # the arrow keys that chose it
            add(Action.SELECT, tg, str(e.get("value", "")), f'select "{e.get("value", "")}" from {tg}', el)
            last_acted = False
        elif t == "check":
            tg = describe_target(el)
            on = bool(e.get("checked"))
            add(Action.CHECK if on else Action.UNCHECK, tg, None, f"{'check' if on else 'uncheck'} {tg}", el)
            last_acted = False
        elif t == "key":
            key = e.get("key") or "Enter"
            tg = describe_target(el) if el else None
            add(Action.PRESS, tg, key, f"press {key}" + (f" in {tg}" if tg else ""), el or None)
            last_acted, last_t = key == "Enter", ts
        elif t == "assert_text":
            add(Action.ASSERT_TEXT, None, str(e.get("value", "")), f'verify the page shows "{e.get("value", "")}"')
            last_acted = False
        elif t == "assert_visible" and el:
            tg = describe_target(el)
            add(Action.ASSERT_VISIBLE, tg, None, f"verify {tg} is visible", el)
        elif t == "hover" and el:
            tg = describe_target(el)
            add(Action.HOVER, tg, None, f"hover over {tg}", el)
        elif t == "wait":
            add(Action.WAIT, None, str(e.get("seconds", 1)), f"wait {e.get('seconds', 1)} seconds")
        else:
            notes.append(f"Skipped an event it doesn't know: {t}")
    if not steps:
        notes.append("Nothing was recorded.")
    return TestCase(name=name, base_url=base_url, steps=steps, data=data, sensitive=list(dict.fromkeys(sens)),
                    source_text="", engine=None), notes


# ---------------------------------------------------------------------------------------------- Chrome DevTools Recorder
_ARIA = re.compile(r'^aria/(?P<name>.*?)(?:\[role="(?P<role>[^"]*)"\])?$', re.S)
_PW_ROLE = {"button": "button", "link": "link", "textbox": "field", "checkbox": "checkbox", "radio": "radio button",
            "combobox": "dropdown", "searchbox": "field", "listbox": "dropdown"}


def _chrome_locators(selectors: list) -> tuple[list[dict], str, str]:
    """-> (candidate locator dicts in our format, accessible name, role) from a Recorder step's selector alternatives."""
    out: list[dict] = []
    name = role = ""
    for group in selectors or []:
        sel = group[-1] if isinstance(group, list) and group else group
        if not isinstance(sel, str):
            continue
        m = _ARIA.match(sel)
        if m:
            name, role = _clean(m.group("name")), (m.group("role") or "").lower()
            if name:
                out.append({"strategy": "role", "value": name, "role": role} if role else {"strategy": "label", "value": name})
        elif sel.startswith("xpath/"):
            out.append({"strategy": "xpath", "value": sel[6:]})
        elif sel.startswith("text/"):
            out.append({"strategy": "text", "value": _clean(sel[5:])})
        elif sel.startswith("pierce/"):
            out.append({"strategy": "css", "value": sel[7:]})
        else:
            out.append({"strategy": "css", "value": sel})
    # most robust first, as the engines order them: ids and attributes before positional paths
    rank = {"role": 0, "text": 2, "css": 1, "xpath": 3}
    out.sort(key=lambda l: (rank.get(l["strategy"], 4), len(l["value"])))
    return out, name, role


def from_chrome_recording(rec: dict | str, name: str | None = None) -> tuple[TestCase, list[str]]:
    """Chrome DevTools > Recorder > Export > JSON  ->  steps."""
    import json as _json
    if isinstance(rec, (str, bytes)):
        rec = _json.loads(rec)
    if not isinstance(rec, dict) or not isinstance(rec.get("steps"), list):
        raise ValueError("That is not a Chrome Recorder export (a JSON object with a 'steps' list).")
    notes: list[str] = []
    steps: list[Step] = []
    data: dict[str, Any] = {}
    sens: list[str] = []
    base_url = None
    skipped: dict[str, int] = {}

    def add(action: Action, target: str | None, value: str | None, raw: str, locs: list[dict]):
        from .models import Locator
        ls = [Locator(strategy=l["strategy"], value=l["value"], role=l.get("role"), description=raw) for l in locs]
        steps.append(Step(action=action, target=target, value=value, raw=raw, source="recorded",
                          locator=ls[0] if ls else None, fallbacks=ls[1:6]))

    def target_of(locs, nm, role) -> str:
        if nm:
            kind = _PW_ROLE.get(role, "")
            return f"{nm} {kind}" if kind and kind not in ("field", "dropdown") and not nm.lower().endswith(kind.split()[0]) else nm
        for l in locs:
            if l["strategy"] == "text":
                return l["value"]
        for l in locs:
            if l["strategy"] == "css":
                m = re.search(r'#([\w-]+)|\[name="?([\w-]+)', l["value"])
                if m:
                    return (m.group(1) or m.group(2)).replace("-", " ").replace("_", " ")
        return "element"

    for s in rec["steps"]:
        t = s.get("type")
        locs, nm, role = _chrome_locators(s.get("selectors") or [])
        if s.get("frame"):
            if not any("iframe" in n for n in notes):
                notes.append("A step inside an iframe was recorded; the frame is searched automatically on replay.")
        if t == "navigate":
            url = s.get("url") or ""
            if url:
                if steps and steps[-1].action == Action.NAVIGATE:
                    steps[-1] = Step(action=Action.NAVIGATE, value=url, raw=f"open {url}", source="recorded")
                else:
                    add(Action.NAVIGATE, None, url, f"open {url}", [])
                base_url = base_url or url
        elif t in ("click", "doubleClick", "hover"):
            tg = target_of(locs, nm, role)
            act, verb = {"click": (Action.CLICK, "click"), "doubleClick": (Action.DOUBLE_CLICK, "double-click"), "hover": (Action.HOVER, "hover over")}[t]
            if t == "click" and s.get("button") == "secondary":
                act, verb = Action.RIGHT_CLICK, "right-click"
            add(act, tg, None, f"{verb} {tg}", locs)
        elif t == "change":
            tg = target_of(locs, nm, role)
            val = str(s.get("value", ""))
            if re.search(r"password|passwd|pwd", f"{tg} " + " ".join(l["value"] for l in locs), re.I):
                k = "password"
                n = 1
                while k in data:
                    n += 1
                    k = f"password{n}"
                data[k] = val
                sens.append(k)
                val = "${" + k + "}"
            add(Action.TYPE, tg, val, f'type "{val}" into {tg}', locs)
        elif t == "keyDown":
            key = s.get("key") or ""
            if key in ENTER_LIKE:
                add(Action.PRESS, None, key, f"press {key}", [])
        elif t == "waitForElement":
            tg = target_of(locs, nm, role)
            add(Action.WAIT, tg, None, f"wait for {tg}", locs)
        elif t == "scroll":
            skipped["scroll"] = skipped.get("scroll", 0) + 1
        elif t in ("keyUp", "setViewport", "emulateNetworkConditions", "close", "waitForExpression"):
            if t in ("waitForExpression", "emulateNetworkConditions"):
                skipped[t] = skipped.get(t, 0) + 1
        else:
            skipped[str(t)] = skipped.get(str(t), 0) + 1
    for k, c in skipped.items():
        notes.append(f"Left out {c} '{k}' step{'s' if c > 1 else ''} (not something a replay needs, or not supported).")
    if not steps:
        notes.append("The recording has no steps.")
    return TestCase(name=name or rec.get("title") or "Chrome recording", base_url=base_url, steps=steps, data=data,
                    sensitive=list(dict.fromkeys(sens))), notes
