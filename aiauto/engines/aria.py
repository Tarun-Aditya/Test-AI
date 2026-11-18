"""Candidates from Playwright's own accessibility snapshot: page.aria_snapshot(mode="ai").

Playwright computes roles and accessible names per the ARIA spec, walks shadow DOM and iframes, and gives
every element a ref ([ref=e12], [ref=f1e3] inside the first iframe). A step's element is chosen from these
candidates, then `page.get_by_ref(ref).normalize()` asks Playwright's own locator generator (the one behind
codegen's "pick locator") for the best unique locator.

Snapshot lines look like:
    - row "#1002 Pending View" [ref=e70]:
      - cell "#1002" [ref=e71]
      - button "View" [ref=e74] [cursor=pointer]
    - combobox "Country" [ref=e40]:
      - option "Choose…" [selected]
      - option "India"
    - link "Home" [ref=e3] [cursor=pointer]:
      - /url: "#"
    - generic [ref=e64]: Card A
"""
from __future__ import annotations

import json
import re

LINE = re.compile(r'^(?P<ind>\s*)- (?P<role>[A-Za-z/][\w/-]*)(?: (?P<name>"(?:[^"\\]|\\.)*"))?(?P<attrs>(?: \[[^\]]*\])*)(?::\s*(?P<rest>.*))?$')
ATTR = re.compile(r"\[([\w-]+)(?:=([^\]]*))?\]")

ROLE_TAG = {"button": ("button", ""), "link": ("a", ""), "textbox": ("input", "text"), "searchbox": ("input", "search"),
            "spinbutton": ("input", "number"), "checkbox": ("input", "checkbox"), "radio": ("input", "radio"),
            "slider": ("input", "range"), "combobox": ("div", ""), "listbox": ("div", ""), "option": ("option", ""),
            "heading": ("h2", ""), "img": ("img", ""), "listitem": ("li", ""), "cell": ("td", ""), "gridcell": ("td", ""),
            "columnheader": ("th", ""), "rowheader": ("th", ""), "row": ("tr", ""), "menuitem": ("li", ""), "tab": ("div", ""),
            "switch": ("input", "checkbox"), "paragraph": ("p", ""), "generic": ("div", "")}
CONTEXT_ROLES = {"row", "listitem", "article", "dialog", "form", "region", "group", "navigation", "banner", "main",
                 "complementary", "list", "table", "menu", "tabpanel", "radiogroup", "heading", "cell"}


def _unq(s: str | None) -> str:
    if not s:
        return ""
    try:
        return json.loads(s)
    except Exception:
        return s.strip('"')


def parse(snapshot: str) -> list[dict]:
    """aria_snapshot(mode='ai') text -> candidate dicts compatible with locators.score()/choose()."""
    out: list[dict] = []
    stack: list[tuple[int, dict | None, str]] = []      # (indent, candidate or None, label for context)
    uid = 0
    for raw in snapshot.splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        ind = len(m["ind"])
        while stack and stack[-1][0] >= ind:
            stack.pop()
        role, name, rest = m["role"], _unq(m["name"]), (m["rest"] or "").strip()
        attrs = {k: (v if v is not None else True) for k, v in ATTR.findall(m["attrs"] or "")}
        parent = next((c for _, c, _ in reversed(stack) if c), None)
        if role == "/url" and parent is not None:
            parent["href"] = _unq(rest) if rest.startswith('"') else rest
            continue
        if role == "option" and parent is not None and parent["role"] in ("combobox", "listbox"):
            parent["options"].append(name or _unq(rest))
            parent["tag"] = "select" if parent["role"] == "combobox" else parent["tag"]
        if role == "text":
            if parent is not None and not parent["text"]:
                parent["text"] = _unq(rest)[:200]
            continue
        ref = attrs.get("ref")
        label = f'{role} "{name}"' if name else (role if role not in ("generic", "group") else "")
        if not ref:
            stack.append((ind, None, label))
            continue
        uid += 1
        tag, typ = ROLE_TAG.get(role, ("div", ""))
        inline = _unq(rest) if rest and not rest.endswith(":") and rest != "" else ""
        ctx = [l for _, _, l in stack if l][-3:]
        c = {
            "uid": uid, "ref": ref, "role": "" if role in ("generic", "paragraph") else role, "aria_role": role,
            "name": name, "text": (inline or name)[:200], "tag": tag, "type": typ, "visible": True,
            "checked": attrs.get("checked"), "disabled": bool(attrs.get("disabled")), "expanded": attrs.get("expanded"),
            "level": attrs.get("level"), "clickable": attrs.get("cursor") == "pointer",
            "editable": role in ("textbox", "searchbox"), "options": [], "href": "",
            "context": " › ".join(ctx), "anc": [c2["uid"] for _, c2, _ in stack if c2],
            "in_frame": bool(re.match(r"f\d+e", ref)), "frames": [], "shadow": [],
            "label": "", "aria_label": "", "placeholder": "", "title": "", "alt": name if role == "img" else "",
            "id": "", "nameattr": "", "testid": "", "value": "", "source": "playwright-aria",
        }
        out.append(c)
        stack.append((ind, c, label))
    return out
