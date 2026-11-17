"""Test data handling: JSON files / inline JSON / key=value and ${var} substitution."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_VAR = re.compile(r"\$\{\s*([\w.\-\[\]]+)\s*\}|\{\{\s*([\w.\-\[\]]+)\s*\}\}")


def load_data(source: str | dict | None) -> dict[str, Any]:
    """Accept a dict, a path to a .json file, an inline JSON string, or 'k=v;k2=v2'."""
    if not source:
        return {}
    if isinstance(source, dict):
        return source
    s = source.strip()
    p = Path(s)
    if len(s) < 1024 and p.suffix.lower() == ".json" and p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    if s.startswith("{") or s.startswith("["):
        data = json.loads(s)
        return data if isinstance(data, dict) else {"rows": data}
    out: dict[str, Any] = {}
    for part in re.split(r"[;\n]", s):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def lookup(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for token in re.findall(r"[^.\[\]]+", path):
        if isinstance(cur, list):
            cur = cur[int(token)]
        elif isinstance(cur, dict) and token in cur:
            cur = cur[token]
        else:
            raise KeyError(path)
    return cur


def substitute(text: str | None, data: dict[str, Any]) -> str | None:
    """Replace ${name} / {{name}} (dotted paths allowed) with values from data.
    Unknown variables are left untouched so the problem is visible in the report."""
    if text is None or not data:
        return text

    def repl(m: re.Match) -> str:
        key = m.group(1) or m.group(2)
        try:
            return str(lookup(data, key))
        except (KeyError, IndexError, ValueError):
            return m.group(0)

    return _VAR.sub(repl, text)


def iterations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Data-driven runs: {"rows": [{...}, {...}]} runs once per row (row keys override globals)."""
    rows = data.get("rows") if isinstance(data, dict) else None
    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        base = {k: v for k, v in data.items() if k != "rows"}
        return [{**base, **r} for r in rows]
    return [data]
