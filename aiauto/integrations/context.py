"""The context pack: everything known about one story, from its tracker, docs and code, bounded and scrubbed."""
from __future__ import annotations

import hashlib
import json
import re
import time

from .base import Connector, IntegrationError, Issue, find_section, items, scrub, sections, wiki_to_text

CAP = {"description": 8000, "doc": 6000, "diff": 12000, "file": 8000}


def build(tracker: Connector, key: str, docs: list[Connector] = (), code: list[Connector] = ()) -> dict:
    return build_for(tracker.get_issue(key), docs, code)


def build_for(issue: Issue, docs: list[Connector] = (), code: list[Connector] = (), notes: str = "", meta: dict | None = None) -> dict:
    """The pack for an issue from a tracker or pasted by hand; notes = extra text pasted next to it (spec, wiki page)."""
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
        warnings += got.get("warnings") or []
        merged["diff"] += got.get("diff") or ""
    issue_d = issue.to_dict()
    issue_d["description"] = scrub(issue_d["description"])[:CAP["description"]]
    issue_d["comments"] = [scrub(c)[:1500] for c in issue_d["comments"]]
    pack = {
        "issue": issue_d,
        "docs": ([{"title": "Pasted notes", "url": "", "text": scrub(notes)[:CAP["doc"]], "pasted": True}] if notes.strip() else []) +
                [{"title": p["title"], "url": p["url"], "text": scrub(p["text"])[:CAP["doc"]]} for p in pages],
        "code": {**merged, "diff": scrub(merged["diff"])[:CAP["diff"]],
                 "files": [{"path": f["path"], "text": scrub(f["text"])[:CAP["file"]]} for f in merged["files"]]},
        "warnings": warnings,
        "built_at": time.time(),
        "sources": [u for u in [issue.url] + [p["url"] for p in pages] + [pr.get("url") for pr in merged["prs"]] if u],
        "meta": meta or {"source": issue.source or "tracker", "auto_sync": issue.source != "pasted"},
    }
    pack["fingerprint"] = fingerprint(pack)
    return pack


def fingerprint(pack: dict) -> str:
    body = {k: v for k, v in pack.items() if k not in ("built_at", "fingerprint", "meta")}
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


def _is_wiki(text: str) -> bool:
    return bool(re.search(r"(?m)^h[1-6]\.\s|\{code|\{noformat|^\|\|", text))


def issue_from_text(text: str, title: str = "", key: str = "", type_: str = "Story") -> Issue:
    """A story or bug pasted by hand (Markdown, Jira wiki markup or plain text) -> Issue, read like a tracker's."""
    raw = text or ""
    desc = wiki_to_text(raw) if _is_wiki(raw) else raw.replace("\r\n", "\n")
    lines = [ln for ln in desc.splitlines() if ln.strip()]
    if not title:
        first = lines[0].strip().lstrip("#").strip() if lines else ""
        title = first[:120] or "Pasted story"
    secs = sections(desc)
    ac = items(find_section(secs, "acceptance", "criteria", "ac", "requirements", "rules"))
    if not ac:   # Given/When/Then anywhere
        ac = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip() for ln in lines if re.match(r"(?i)^\s*(?:[-*•]|\d+[.)])?\s*given\b", ln)]
    if not ac:   # no headings at all: every bullet / numbered line is a criterion
        bullets = [ln for ln in lines if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", ln)]
        if bullets and not find_section(secs, "steps to reproduce", "repro", "steps"):
            ac = items(bullets)
    key = (key or "").strip().upper()
    return Issue(key=key or "LOCAL", title=title.strip(), type=type_ or "Story", status="", url="", description=desc,
                 acceptance=ac, steps_to_reproduce=items(find_section(secs, "steps to reproduce", "repro", "steps")),
                 expected_result=" ".join(find_section(secs, "expected")), actual_result=" ".join(find_section(secs, "actual")),
                 source="pasted")
