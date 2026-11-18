"""Chrome's own accessibility tree (CDP) for Selenium.

SNAPSHOT_JS approximates each element's ARIA role and accessible name in JavaScript. On Chromium
browsers Selenium can ask the browser for the real values over the DevTools Protocol, which follows
the full accessible-name rules (aria-labelledby chains, <label> wrapping, alt/title, hidden content…).

    1. tag the snapshot's elements with a temporary data attribute (window.__aiautoEls)
    2. DOM.getDocument(depth=-1, pierce=True)   -> attribute -> backendNodeId  (shadow roots + iframes)
    3. Accessibility.getFullAXTree per frame      -> backendNodeId -> (role, name)
    4. remove the attribute, overwrite role/name on the candidates, rebuild their role locator

Any failure leaves the candidates exactly as SNAPSHOT_JS produced them.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

ATTR = "data-aiauto-uid"
TAG_JS = f"""const els = window.__aiautoEls || []; els.forEach((el, i) => {{ try {{ el.setAttribute('{ATTR}', String(i + 1)); }} catch (e) {{}} }});
return els.length;"""
UNTAG_JS = f"""(window.__aiautoEls || []).forEach(el => {{ try {{ el.removeAttribute('{ATTR}'); }} catch (e) {{}} }});"""

# Chrome AX role names that differ from ARIA / Playwright role names
ROLE_MAP = {"image": "img", "StaticText": None, "generic": None, "none": None, "presentation": None,
            "LabelText": None, "paragraph": None, "RootWebArea": None, "Iframe": None, "IframePresentational": None,
            "InlineTextBox": None, "LineBreak": None, "ListMarker": None, "Section": None, "group": None,
            "Canvas": None, "Pre": None, "Abbr": None, "Ruby": None, "time": None, "DescriptionListTerm": None,
            "DescriptionListDetail": None, "contentinfo": None, "banner": None, "main": None, "navigation": None,
            "form": None, "region": None, "list": None, "table": None, "row": None, "rowgroup": None}


# Only standard ARIA roles are usable in get_by_role / role XPath; Chrome-internal ones (Date, ColorWell, Legend…) are not.
ARIA_ROLES = {"button", "link", "textbox", "searchbox", "combobox", "listbox", "option", "checkbox", "radio", "switch",
              "slider", "spinbutton", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "tabpanel", "heading", "img",
              "cell", "columnheader", "rowheader", "gridcell", "listitem", "dialog", "alert", "alertdialog", "treeitem",
              "progressbar", "meter", "separator", "tooltip", "status", "article", "figure", "term", "definition", "note",
              "toolbar", "menu", "menubar", "tablist", "tree", "grid", "radiogroup", "search"}


def _walk_dom(node: dict, out: dict) -> None:
    attrs = node.get("attributes") or []
    for i in range(0, len(attrs) - 1, 2):
        if attrs[i] == ATTR:
            out[int(attrs[i + 1])] = node["backendNodeId"]
            break
    for key in ("children", "shadowRoots"):
        for ch in node.get(key) or []:
            _walk_dom(ch, out)
    if node.get("contentDocument"):
        _walk_dom(node["contentDocument"], out)
    for key in ("templateContent", "importedDocument"):
        if node.get(key):
            _walk_dom(node[key], out)


def _frame_ids(tree: dict) -> list[str]:
    ids = [tree["frame"]["id"]]
    for ch in tree.get("childFrames") or []:
        ids += _frame_ids(ch)
    return ids


def _val(prop: dict | None) -> str:
    return str((prop or {}).get("value") or "")


def refine(cdp, candidates: list[dict]) -> int:
    """Chrome's role + accessible name for just these (top-ranked) candidates.

    One Runtime.evaluate (element -> remote object) and one Accessibility.getPartialAXTree per candidate:
    a few ms each, independent of page size (the full tree costs seconds on large pages)."""
    hits = 0
    group = "aiauto-ax"
    try:
        for c in candidates:
            try:
                res = cdp("Runtime.evaluate", {"expression": f"(window.__aiautoEls || [])[{int(c['uid']) - 1}]", "objectGroup": group})
                oid = (res.get("result") or {}).get("objectId")
                if not oid:
                    continue
                nodes = cdp("Accessibility.getPartialAXTree", {"objectId": oid, "fetchRelatives": False}).get("nodes", [])
            except Exception as e:
                log.debug("AX lookup failed for %s: %s", c.get("uid"), e)
                continue
            node = next((n for n in nodes if not n.get("ignored")), None)
            if not node:
                continue
            _apply(c, _val(node.get("role")), " ".join(_val(node.get("name")).split()))
            hits += 1
    finally:
        try:
            cdp("Runtime.releaseObjectGroup", {"objectGroup": group})
        except Exception:
            pass
    return hits


def _apply(c: dict, role: str, name: str) -> None:
    role = ROLE_MAP.get(role, role)
    if role not in ARIA_ROLES or (c["tag"] == "input" and c.get("type") == "file"):
        role = None                     # keep the snapshot's role; still use Chrome's accessible name
    if role and role != c.get("role"):
        c["js_role"], c["role"] = c.get("role"), role
    if name and name != c.get("name"):
        c["js_name"], c["name"] = c.get("name"), name[:200]
    c["ax"] = True


def enrich(cdp, run_js, candidates: list[dict]) -> int:
    """Whole-page variant (full accessibility tree). Kept for diagnostics; refine() is used per step."""
    """cdp(cmd, params) -> dict and run_js(script) -> value are the engine's CDP / execute_script calls.
    Returns how many candidates got their role/name from the accessibility tree."""
    if not candidates:
        return 0
    t0 = time.time()
    if not run_js(TAG_JS):
        return 0
    try:
        doc = cdp("DOM.getDocument", {"depth": -1, "pierce": True})
        uid_to_bid: dict[int, int] = {}
        _walk_dom(doc["root"], uid_to_bid)
    finally:
        try:
            run_js(UNTAG_JS)
        except Exception:
            pass
    ax: dict[int, tuple[str, str]] = {}
    try:
        frames = _frame_ids(cdp("Page.getFrameTree", {})["frameTree"])
    except Exception:
        frames = [None]
    for fid in frames:
        try:
            nodes = cdp("Accessibility.getFullAXTree", {"frameId": fid} if fid else {}).get("nodes", [])
        except Exception as e:          # e.g. a cross-origin (out-of-process) iframe
            log.debug("AX tree unavailable for frame %s: %s", fid, e)
            continue
        for n in nodes:
            bid = n.get("backendDOMNodeId")
            if bid is None or n.get("ignored"):
                continue
            ax[bid] = (_val(n.get("role")), " ".join(_val(n.get("name")).split()))

    hits = 0
    for c in candidates:
        bid = uid_to_bid.get(c["uid"])
        if bid is None or bid not in ax:
            continue
        role, name = ax[bid]
        role = ROLE_MAP.get(role, role)
        if role not in ARIA_ROLES or (c["tag"] == "input" and c.get("type") == "file"):
            role = None                     # keep the snapshot's role; still use Chrome's accessible name
        changed = False
        if role and role != c.get("role"):
            c["js_role"], c["role"], changed = c.get("role"), role, True
        if name and name != c.get("name"):
            c["js_name"], c["name"], changed = c.get("name"), name[:200], True
        c["ax"] = True
        hits += 1
        if changed:
            _rebuild_role_locator(c)
    log.debug("AX enrich: %d/%d candidates in %.0f ms", hits, len(candidates), (time.time() - t0) * 1000)
    return hits


def _rebuild_role_locator(c: dict) -> None:
    locs = [l for l in c.get("locators", []) if l["strategy"] != "role"]
    if c.get("role") and c.get("name") and len(c["name"]) <= 80:
        # same priority as SNAPSHOT_JS: after test-ids / data-* attributes / stable id
        pos = 0
        while pos < len(locs) and (locs[pos]["strategy"] in ("testid", "id") or locs[pos]["value"].startswith("[data-")):
            pos += 1
        locs.insert(pos, {"strategy": "role", "value": c["name"], "role": c["role"]})
    c["locators"] = locs
