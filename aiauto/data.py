"""Test data: loading (JSON file / inline JSON / k=v), ${var} substitution, sensitive values & masking.

Marking data as sensitive (any of these):
  {"password": {"value": "s3cret", "sensitive": true}}      inline marker
  {"password": "s3cret", "__sensitive__": ["password"]}     list of dotted keys
  sensitive=["user.password"] passed alongside the data    (web UI checkbox / API field)

Sensitive values are masked in live events, reports, summary.json, data.json, script.json and the
generated scripts (which read them from environment variables instead). When a sensitive value is
missing at run time (e.g. replaying a script whose secrets were stripped), it is looked up in the
executing machine's environment as AIAUTO_SECRET_<KEY> (dots -> underscores, upper-case), so
secrets can live only on the agent machine.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

_VAR = re.compile(r"\$\{\s*([\w.\-\[\]]+)\s*\}|\{\{\s*([\w.\-\[\]]+)\s*\}\}")
MASK = "••••••"


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


def normalize(data: dict[str, Any], sensitive: Iterable[str] = ()) -> tuple[dict[str, Any], list[str]]:
    """Strip inline {"value":..,"sensitive":..} markers; return (plain data, sorted sensitive keys)."""
    keys = set(sensitive or [])
    keys.update(data.get("__sensitive__", []) if isinstance(data, dict) else [])

    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            if set(obj) <= {"value", "sensitive"} and "value" in obj and "sensitive" in obj:
                if obj.get("sensitive"):
                    keys.add(prefix.rstrip("."))
                return walk(obj["value"], prefix)
            return {k: walk(v, f"{prefix}{k}.") for k, v in obj.items() if k != "__sensitive__"}
        if isinstance(obj, list):
            return [walk(v, f"{prefix}{i}.") for i, v in enumerate(obj)]
        return obj

    plain = walk(data or {})
    # rows: "rows.0.password" style keys also mean "password" in every row
    for k in list(keys):
        m = re.match(r"rows\.\d+\.(.+)", k)
        if m:
            keys.add(m.group(1))
    return plain, sorted(keys)


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


def setpath(data: dict, path: str, value: Any) -> dict:
    """Return a copy of data with the dotted path set (creating dicts as needed)."""
    import copy
    out = copy.deepcopy(data or {})
    keys = path.split(".")
    cur = out
    for k in keys[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value
    return out


def env_secret_name(key: str) -> str:
    return "AIAUTO_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", key).upper()


def _resolve(data: dict, key: str) -> Any:
    try:
        v = lookup(data, key)
    except (KeyError, IndexError, ValueError):
        v = None
    if v is None or v == MASK:
        env = os.getenv(env_secret_name(key))
        if env is not None:
            return env
        if v is None:
            raise KeyError(key)
    return v


def substitute(text: str | None, data: dict[str, Any]) -> str | None:
    """Replace ${name} / {{name}} (dotted paths allowed) with values from data (or AIAUTO_SECRET_* env).
    Unknown variables are left untouched so the problem is visible in the report."""
    if text is None:
        return text

    def repl(m: re.Match) -> str:
        key = m.group(1) or m.group(2)
        try:
            return str(_resolve(data or {}, key))
        except (KeyError, IndexError, ValueError):
            return m.group(0)

    return _VAR.sub(repl, text)


def referenced_keys(text: str | None) -> list[str]:
    return [m.group(1) or m.group(2) for m in _VAR.finditer(text or "")]


def secret_values(data: dict[str, Any], sensitive: Iterable[str]) -> list[str]:
    out = []
    for k in sensitive or []:
        try:
            v = _resolve(data, k)
        except (KeyError, IndexError, ValueError):
            continue
        if v not in (None, "", MASK):
            out.append(str(v))
    return sorted(set(out), key=len, reverse=True)


class Masker:
    """Replaces sensitive values inside any text (errors, page text, typed values)."""

    def __init__(self, data: dict[str, Any], sensitive: Iterable[str]):
        self.sensitive = set(sensitive or [])
        self.values = [v for v in secret_values(data, self.sensitive) if len(v) >= 1]

    def __call__(self, text: Any) -> Any:
        if not isinstance(text, str) or not self.values:
            return text
        for v in self.values:
            text = text.replace(v, MASK)
        return text

    def is_sensitive_template(self, template: str | None) -> bool:
        return any(k in self.sensitive for k in referenced_keys(template))


def masked_data(data: dict[str, Any], sensitive: Iterable[str]) -> dict[str, Any]:
    """Copy of data with sensitive keys replaced by MASK (for artifacts)."""
    sens = set(sensitive or [])

    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            return {k: (MASK if f"{prefix}{k}" in sens or (prefix.startswith("rows.") and k in sens)
                        else walk(v, f"{prefix}{k}.")) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(v, f"{prefix}{i}.") for i, v in enumerate(obj)]
        return obj

    return walk(data)


def iterations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Data-driven runs: {"rows": [{...}, {...}]} runs once per row (row keys override globals)."""
    rows = data.get("rows") if isinstance(data, dict) else None
    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        base = {k: v for k, v in data.items() if k != "rows"}
        return [{**base, **r} for r in rows]
    return [data]
