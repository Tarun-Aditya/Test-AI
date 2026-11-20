"""Front door: plain English (+ data) -> TestCase."""
from __future__ import annotations

import logging
import re

from ..data import normalize
from ..models import Step, TestCase
from . import llm, narrative, rules

log = logging.getLogger(__name__)


def parse_text(text: str, data: dict | None = None, name: str | None = None, use_llm: bool = True,
               sensitive: list[str] | None = None) -> tuple[TestCase, dict]:
    """Returns the TestCase plus info about how it was parsed."""
    data, sensitive = normalize(data or {}, sensitive or [])
    data_in = dict(data)
    info: dict = {"parser": "rules", "unparsed": [], "ignored": [], "notes": []}
    steps: list[Step] = []
    test_name = name

    if use_llm and llm.available():
        result = llm.parse_steps(text, list(_flatten_keys(data)))
        if result and result[1]:
            test_name = test_name or result[0]
            steps = result[1]
            info["parser"] = f"llm:{llm.model_label()}"

    if not steps:
        got = rules.parse_detailed(text)
        steps = got["steps"]
        narrative.lift_secrets(steps, got["data"], got["sensitive"])      # a password written into the text is masked data, not a step value
        info.update(unparsed=got["unparsed"], ignored=got["ignored"], notes=got["notes"])
        if got["unparsed"]:
            log.warning("Could not understand: %s", got["unparsed"])
        # values the paragraph gave without a field of their own: passwords become masked data, the page address a blank to fill
        for k, v in got["data"].items():
            if k not in data:
                data[k] = v
        sensitive = sorted(set(sensitive) | set(got["sensitive"]))
        info["data_added"] = {k: ("••••••" if k in got["sensitive"] else v) for k, v in got["data"].items()}

    if info["parser"] != "rules":                  # the AI wrote the steps: the same secret handling as for the rules
        extra: dict = {}
        sens_extra: list[str] = []
        narrative.lift_secrets(steps, extra, sens_extra, only_derived=False)
        for k, v in extra.items():
            data.setdefault(k, v)
        sensitive = sorted(set(sensitive) | set(sens_extra))
        info["notes"] += narrative.ensure_secret_values(steps, data, sensitive)
        info["data_added"] = {k: "••••••" for k in data if k in sensitive and k not in (data_in or {})}

    if not test_name:
        test_name = _default_name(steps, text)

    base_url = next((s.value for s in steps if s.action.value == "navigate"), None)
    return TestCase(name=test_name, base_url=base_url, steps=steps, data=data, sensitive=sensitive, source_text=text), info


def _flatten_keys(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                yield from _flatten_keys(v, key + ".")
            else:
                yield key


def _default_name(steps: list[Step], text: str) -> str:
    """A readable name from what the test does, e.g.
    'localhost:8711 · Search "wireless mouse" → Add to Cart → check "Logitech"'.
    Placeholders like ${brand} are kept here and filled with real values per run (server.display_name)."""
    from urllib.parse import urlparse
    host, parts = "", []
    for st in steps:
        a, t, v = st.action.value, st.target or "", st.value or ""
        if a == "navigate" and not host and v:
            host = urlparse(v).netloc if "://" in v else ("" if v.startswith("${") else v)
        elif a == "type" and "search" in t.lower():
            parts.append(f'Search "{v}"')
        elif a == "type" and "password" in t.lower():
            parts.append("Log in")
        elif a in ("click", "select", "check", "drag") and t and not re.search(r"\b(?:log\s*in|sign\s*in|cookies?|close)\b", t, re.I):
            name = re.sub(r"\s+(?:button|link|menu|field|dropdown|tab)$", "", t, flags=re.I)
            ordw = {0: "first", 1: "second", 2: "third", -1: "last"}.get(st.nth, f"#{(st.nth or 0) + 1}") if st.nth is not None else ""
            parts.append(f"{ordw} {name} result" if ordw else name if a != "select" else f"{name} {v}")
    asserts = [st.value for st in steps if st.action.value == "assert_text" and st.value]
    if asserts:
        parts.append(f'check "{asserts[-1][:30]}"')
    dedup = []
    for p in parts:
        if not dedup or dedup[-1] != p:
            dedup.append(p)
    if len(dedup) > 4:
        dedup = dedup[:2] + ["…"] + dedup[-2:]
    if dedup:
        return ((host + " · ") if host else "") + " → ".join(dedup)
    first = text.strip().splitlines()[0] if text.strip() else "Untitled test"
    return re.sub(r"\s+", " ", first)[:70]
