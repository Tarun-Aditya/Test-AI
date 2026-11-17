"""Front door: plain English (+ data) -> TestCase."""
from __future__ import annotations

import logging
import re

from ..data import normalize
from ..models import Step, TestCase
from . import llm, rules

log = logging.getLogger(__name__)


def parse_text(text: str, data: dict | None = None, name: str | None = None, use_llm: bool = True,
               sensitive: list[str] | None = None) -> tuple[TestCase, dict]:
    """Returns the TestCase plus info about how it was parsed."""
    data, sensitive = normalize(data or {}, sensitive or [])
    info: dict = {"parser": "rules", "unparsed": []}
    steps: list[Step] = []
    test_name = name

    if use_llm and llm.available():
        result = llm.parse_steps(text, list(_flatten_keys(data)))
        if result and result[1]:
            test_name = test_name or result[0]
            steps = result[1]
            info["parser"] = f"llm:{llm.MODEL}"

    if not steps:
        steps, unparsed = rules.parse(text)
        info["unparsed"] = unparsed
        if unparsed:
            log.warning("Could not understand: %s", unparsed)

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
    """'shop.example.com: Add to Cart, Go to Cart' style name from the URL and the clicks."""
    from urllib.parse import urlparse
    host = ""
    for st in steps:
        if st.action.value == "navigate" and st.value:
            host = urlparse(st.value).netloc or ("" if "${" in st.value else st.value)
            break
    clicks = [st.target for st in steps if st.action.value in ("click", "type", "select") and st.target and "${" not in st.target][-2:]
    if clicks:
        return (f"{host}: " if host else "") + " → ".join(c[:30] for c in clicks)
    first = text.strip().splitlines()[0] if text.strip() else "Untitled test"
    return re.sub(r"\s+", " ", first)[:60]
