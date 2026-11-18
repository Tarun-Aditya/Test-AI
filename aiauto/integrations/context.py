"""The context pack: everything known about one story, from its tracker, docs and code, bounded and scrubbed."""
from __future__ import annotations

import hashlib
import json
import time

from .base import Connector, IntegrationError, Issue, scrub

CAP = {"description": 8000, "doc": 6000, "diff": 12000, "file": 8000}


def build(tracker: Connector, key: str, docs: list[Connector] = (), code: list[Connector] = ()) -> dict:
    issue = tracker.get_issue(key)
    warnings: list[str] = []
    pages = []
    for d in docs:
        try:
            pages += d.pages_for(issue)
        except IntegrationError as e:
            warnings.append(str(e))
    merged = {"prs": [], "diff": "", "files": [], "fields": [], "buttons": [], "pages": [], "routes": []}
    for c in code:
        try:
            got = c.context_for(issue)
        except IntegrationError as e:
            warnings.append(str(e))
            continue
        for k in ("prs", "files", "fields", "buttons", "pages", "routes"):
            merged[k] += got.get(k) or []
        merged["diff"] += got.get("diff") or ""
    issue_d = issue.to_dict()
    issue_d["description"] = scrub(issue_d["description"])[:CAP["description"]]
    issue_d["comments"] = [scrub(c)[:1500] for c in issue_d["comments"]]
    pack = {
        "issue": issue_d,
        "docs": [{"title": p["title"], "url": p["url"], "text": scrub(p["text"])[:CAP["doc"]]} for p in pages],
        "code": {**merged, "diff": scrub(merged["diff"])[:CAP["diff"]],
                 "files": [{"path": f["path"], "text": scrub(f["text"])[:CAP["file"]]} for f in merged["files"]]},
        "warnings": warnings,
        "built_at": time.time(),
        "sources": [issue.url] + [p["url"] for p in pages] + [pr["url"] for pr in merged["prs"]],
    }
    pack["fingerprint"] = fingerprint(pack)
    return pack


def fingerprint(pack: dict) -> str:
    body = {k: v for k, v in pack.items() if k not in ("built_at", "fingerprint")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def issue_of(pack: dict) -> Issue:
    return Issue(**{k: v for k, v in pack["issue"].items() if k in Issue.__dataclass_fields__})


def summary_text(pack: dict, limit: int = 14000) -> str:
    """The pack as text, for the AI and for people reading it."""
    i = pack["issue"]
    parts = [f"STORY {i['key']} ({i.get('type') or 'issue'}): {i['title']}", i.get("description") or ""]
    if i.get("acceptance"):
        parts.append("ACCEPTANCE CRITERIA:\n" + "\n".join(f"{n}. {a}" for n, a in enumerate(i["acceptance"], 1)))
    if i.get("steps_to_reproduce"):
        parts.append("STEPS TO REPRODUCE:\n" + "\n".join(i["steps_to_reproduce"]))
    for d in pack.get("docs") or []:
        parts.append(f"DOC {d['title']}:\n{d['text'][:3000]}")
    c = pack.get("code") or {}
    if c.get("fields"):
        parts.append("FORM FIELDS IN THE CODE:\n" + "\n".join(json.dumps(f) for f in c["fields"]))
    if c.get("buttons"):
        parts.append("BUTTONS: " + ", ".join(c["buttons"]))
    if c.get("pages") or c.get("routes"):
        parts.append("PAGES/ROUTES: " + ", ".join((c.get("pages") or []) + (c.get("routes") or [])))
    if c.get("diff"):
        parts.append("DIFF:\n" + c["diff"][:5000])
    return "\n\n".join(p for p in parts if p)[:limit]
