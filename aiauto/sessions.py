"""Saved login sessions (cookies + localStorage in Playwright storage_state format).

A session captured with either engine can be loaded by either engine, so you can log in once
(e.g. headed, with a `Pause 60 seconds for SSO login` step and "save session as") and reuse the
logged-in state in later headless Playwright *or* Selenium runs, on any agent.
Session files contain authentication tokens: they live outside the artifacts folder, are never
embedded in reports, and the hub only hands them to agents when a run asks for them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(os.getenv("AIAUTO_SESSIONS", "sessions"))


def _path(name: str) -> Path:
    if not re.fullmatch(r"[\w.\- ]{1,80}", name or ""):
        raise ValueError("invalid session name")
    return ROOT / f"{name}.json"


def save(name: str, state: dict) -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    p = _path(name)
    p.write_text(json.dumps(state))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def load(name: str) -> dict | None:
    p = _path(name)
    return json.loads(p.read_text()) if p.exists() else None


def delete(name: str) -> None:
    _path(name).unlink(missing_ok=True)


def list_all() -> list[dict]:
    if not ROOT.exists():
        return []
    out = []
    for p in sorted(ROOT.glob("*.json")):
        try:
            st = json.loads(p.read_text())
            domains = sorted({c.get("domain", "").lstrip(".") for c in st.get("cookies", [])})
        except Exception:
            domains = []
        out.append({"name": p.stem, "domains": domains[:10], "updated": int(p.stat().st_mtime)})
    return out
