"""Jira (Cloud or Data Center) with a token: Cloud = e-mail + API token (Basic), Data Center = personal access
token (Bearer). Reads stories with their acceptance criteria, links and comments; writes comments, status
transitions, bugs and attachments."""
from __future__ import annotations

import re

from .base import (Connector, Http, IntegrationError, Issue, basic, bearer, find_section, items, sections,
                   wiki_to_text)

KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
FIELDS = "summary,status,issuetype,labels,components,description,issuelinks,subtasks,parent,comment,attachment"


class Jira(Connector):
    kind, label, roles, status = "jira", "Jira", ("tracker",), "standard"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        base = (self.s.get("base_url") or "").rstrip("/")
        if not base:
            raise IntegrationError("Jira: set the site URL")
        self.cloud = (self.s.get("deployment") or ("cloud" if ".atlassian.net" in base else "datacenter")) == "cloud"
        token = self.secrets.get("token") or ""
        if not token:
            raise IntegrationError("Jira: add an API token (Cloud) or a personal access token (Data Center)")
        if self.cloud and not self.s.get("email"):
            raise IntegrationError("Jira Cloud: the API token needs the e-mail of its Atlassian account")
        self.base = base
        self.http = Http(base, basic(self.s["email"], token) if self.cloud else bearer(token), "Jira")
        self.project = (self.s.get("project_key") or "").strip()

    def browse(self, key: str) -> str:
        return f"{self.base}/browse/{key}"

    def test(self) -> str:
        me = self.http.json("GET", "/rest/api/2/myself")
        who = me.get("displayName") or me.get("name") or me.get("emailAddress") or "?"
        if self.project:
            p = self.http.json("GET", f"/rest/api/2/project/{self.project}")
            return f"signed in as {who}; project {p.get('key')} ({p.get('name')})"
        return f"signed in as {who}"

    # ---------------------------------------------------------------- search / read
    def _jql(self, query: str) -> str:
        q = (query or "").strip()
        if KEY_RX.match(q.upper()):
            return f"key = {q.upper()}"
        scope = f'project = "{self.project}"' if self.project else ""
        if re.search(r"(?i)\b(?:project|status|issuetype|type|sprint|labels|assignee|fixVersion|key|text|summary)\s*(?:=|!=|~|in\b)|order\s+by", q):
            return f"{scope} AND ({q})" if scope and "project" not in q.lower() else q
        text = q.replace('"', '\\"')
        base = f'{scope} AND text ~ "{text}"' if text and scope else (f'text ~ "{text}"' if text else scope)
        return f"{base} ORDER BY updated DESC" if base else "ORDER BY updated DESC"

    def search(self, query: str, limit: int = 25) -> list[Issue]:
        jql = self._jql(query)
        if self.cloud:   # Cloud: the JQL search endpoint (token-paged)
            data = self.http.json("GET", "/rest/api/3/search/jql", params={"jql": jql, "maxResults": limit,
                                                                           "fields": "summary,status,issuetype,labels"})
        else:
            data = self.http.json("GET", "/rest/api/2/search", params={"jql": jql, "maxResults": limit,
                                                                       "fields": "summary,status,issuetype,labels"})
        out = []
        for it in (data or {}).get("issues", [])[:limit]:
            f = it.get("fields") or {}
            out.append(Issue(key=it["key"], id=str(it.get("id", "")), title=f.get("summary") or "",
                             type=(f.get("issuetype") or {}).get("name", ""), status=(f.get("status") or {}).get("name", ""),
                             labels=f.get("labels") or [], url=self.browse(it["key"]), source="jira"))
        return out

    def get_issue(self, key: str) -> Issue:
        data = self.http.json("GET", f"/rest/api/2/issue/{key}", params={"fields": f"{FIELDS},*navigable", "expand": "names"})
        f, names = data.get("fields") or {}, data.get("names") or {}
        desc = wiki_to_text(f.get("description") if isinstance(f.get("description"), str) else "")
        secs = sections(desc)
        # acceptance criteria: the configured field, a field named like it, or the description's section
        ac_field = self.s.get("ac_field") or next((fid for fid, n in names.items() if re.search(r"(?i)acceptance", n or "")), None)
        ac_raw = f.get(ac_field) if ac_field else None
        if isinstance(ac_raw, dict):          # rich text in Cloud custom fields
            ac_raw = _adf_text(ac_raw)
        ac = items(wiki_to_text(ac_raw).splitlines()) if isinstance(ac_raw, str) and ac_raw.strip() else \
            items(find_section(secs, "acceptance", "criteria", "ac"))
        if not ac:   # Given/When/Then written anywhere in the description
            ac = [ln.strip("-* ") for ln in desc.splitlines() if re.match(r"(?i)^\s*[-*\d.]*\s*given\b", ln)]
        links = []
        for ln in f.get("issuelinks") or []:
            other, rel = (ln.get("outwardIssue"), ln["type"].get("outward")) if ln.get("outwardIssue") else \
                (ln.get("inwardIssue"), ln["type"].get("inward"))
            if other:
                links.append({"key": other["key"], "title": (other.get("fields") or {}).get("summary", ""), "relation": rel})
        for st in f.get("subtasks") or []:
            links.append({"key": st["key"], "title": (st.get("fields") or {}).get("summary", ""), "relation": "subtask"})
        if f.get("parent"):
            p = f["parent"]
            links.append({"key": p["key"], "title": (p.get("fields") or {}).get("summary", ""), "relation": "parent"})
        remote = []
        try:
            for rl in self.http.json("GET", f"/rest/api/2/issue/{key}/remotelink") or []:
                o = rl.get("object") or {}
                if o.get("url"):
                    remote.append({"url": o["url"], "title": o.get("title") or ""})
        except IntegrationError:
            pass
        comments = [wiki_to_text(c.get("body") if isinstance(c.get("body"), str) else "")
                    for c in ((f.get("comment") or {}).get("comments") or [])[-5:]]
        return Issue(
            key=data["key"], id=str(data.get("id", "")), title=f.get("summary") or "", type=(f.get("issuetype") or {}).get("name", ""),
            status=(f.get("status") or {}).get("name", ""), url=self.browse(data["key"]), description=desc, acceptance=ac,
            steps_to_reproduce=items(find_section(secs, "steps to reproduce", "repro", "steps")),
            expected_result=" ".join(find_section(secs, "expected")), actual_result=" ".join(find_section(secs, "actual")),
            labels=f.get("labels") or [], components=[c.get("name", "") for c in f.get("components") or []],
            links=links, remote_links=remote, comments=[c for c in comments if c],
            attachments=[a.get("filename", "") for a in f.get("attachment") or []], source="jira")

    # ---------------------------------------------------------------- write back
    def comment(self, key: str, text: str) -> None:
        self.http.post(f"/rest/api/2/issue/{key}/comment", json={"body": text})

    def transition(self, key: str, to_status: str) -> str:
        ts = (self.http.json("GET", f"/rest/api/2/issue/{key}/transitions") or {}).get("transitions", [])
        want = to_status.strip().lower()
        t = next((t for t in ts if t.get("name", "").lower() == want or (t.get("to") or {}).get("name", "").lower() == want), None)
        if not t:
            raise IntegrationError(f"Jira: {key} can't move to '{to_status}' from its current status "
                                   f"(possible: {', '.join(x.get('name', '') for x in ts) or 'none'})")
        self.http.post(f"/rest/api/2/issue/{key}/transitions", json={"transition": {"id": t["id"]}})
        return (t.get("to") or {}).get("name") or t.get("name")

    def create_bug(self, summary: str, description: str, story: Issue, labels: list[str]) -> str:
        project = self.project or story.key.split("-")[0]
        bug = self.http.json("POST", "/rest/api/2/issue", json={"fields": {
            "project": {"key": project}, "issuetype": {"name": self.s.get("bug_type") or "Bug"},
            "summary": summary[:250], "description": description, "labels": labels}})
        try:
            self.http.post("/rest/api/2/issueLink", json={"type": {"name": self.s.get("bug_link") or "Relates"},
                                                         "inwardIssue": {"key": bug["key"]}, "outwardIssue": {"key": story.key}})
        except IntegrationError:
            pass                                   # link type names differ per site; the bug still exists
        return bug["key"]

    def find_bug(self, label: str) -> str | None:
        jql = f'labels = "{label}" AND statusCategory != Done'
        path, params = ("/rest/api/3/search/jql", {"jql": jql, "maxResults": 1, "fields": "summary"}) if self.cloud else \
            ("/rest/api/2/search", {"jql": jql, "maxResults": 1, "fields": "summary"})
        got = (self.http.json("GET", path, params=params) or {}).get("issues") or []
        return got[0]["key"] if got else None

    def attach(self, key: str, filename: str, data: bytes) -> None:
        self.http.post(f"/rest/api/2/issue/{key}/attachments", headers={"X-Atlassian-Token": "no-check"},
                       files={"file": (filename, data)})


def _adf_text(node) -> str:
    """Atlassian Document Format (rich-text custom fields on Cloud) -> text, list items on their own lines."""
    if isinstance(node, dict):
        t = node.get("type")
        inner = "".join(_adf_text(c) for c in node.get("content") or [])
        if t == "text":
            return node.get("text", "")
        if t in ("paragraph", "heading"):
            return inner + "\n"
        if t == "listItem":
            return "- " + inner.strip() + "\n"
        return inner
    if isinstance(node, list):
        return "".join(_adf_text(c) for c in node)
    return ""
