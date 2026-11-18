"""Confluence (Cloud or Data Center) with a token: the same credentials as Jira. Reads the pages linked from a
story, plus pages found by searching the configured spaces."""
from __future__ import annotations

import re

from .base import Connector, Http, IntegrationError, Issue, basic, bearer, html_to_text

PAGE_ID = re.compile(r"(?:pageId=|/pages/(?:viewpage\.action\?pageId=)?)(\d+)")
MAX_PAGE_CHARS = 12000


class Confluence(Connector):
    kind, label, roles, status = "confluence", "Confluence", ("docs",), "standard"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        base = (self.s.get("base_url") or "").rstrip("/")
        if not base:
            raise IntegrationError("Confluence: set the site URL")
        self.cloud = (self.s.get("deployment") or ("cloud" if ".atlassian.net" in base else "datacenter")) == "cloud"
        token = self.secrets.get("token") or ""
        if not token:
            raise IntegrationError("Confluence: add an API token (Cloud) or a personal access token (Data Center)")
        if self.cloud and not self.s.get("email"):
            raise IntegrationError("Confluence Cloud: the API token needs the e-mail of its Atlassian account")
        # Cloud pages live under /wiki on the site; Data Center at the server root (or its context path)
        self.wiki = base if (not self.cloud or base.endswith("/wiki")) else base + "/wiki"
        self.http = Http(self.wiki, basic(self.s["email"], token) if self.cloud else bearer(token), "Confluence")
        self.spaces = [x.strip() for x in (self.s.get("spaces") or "").split(",") if x.strip()]

    def test(self) -> str:
        me = self.http.json("GET", "/rest/api/user/current")
        return f"signed in as {me.get('displayName') or me.get('username') or me.get('publicName') or '?'}" + \
            (f"; spaces {', '.join(self.spaces)}" if self.spaces else "")

    def page(self, page_id: str) -> dict:
        if self.cloud:
            p = self.http.json("GET", f"/api/v2/pages/{page_id}", params={"body-format": "storage"})
            body = ((p.get("body") or {}).get("storage") or {}).get("value", "")
            link = (p.get("_links") or {}).get("webui", "")
        else:
            p = self.http.json("GET", f"/rest/api/content/{page_id}", params={"expand": "body.storage"})
            body = ((p.get("body") or {}).get("storage") or {}).get("value", "")
            link = (p.get("_links") or {}).get("webui", "")
        return {"id": str(page_id), "title": p.get("title", ""), "url": (self.wiki + link) if link.startswith("/") else link,
                "text": html_to_text(body)[:MAX_PAGE_CHARS]}

    def _search(self, cql: str, limit: int) -> list[str]:
        if self.cloud:
            res = self.http.json("GET", "/rest/api/search", params={"cql": cql, "limit": limit}).get("results", [])
            return [str((r.get("content") or {}).get("id")) for r in res if (r.get("content") or {}).get("id")]
        res = self.http.json("GET", "/rest/api/content/search", params={"cql": cql, "limit": limit}).get("results", [])
        return [str(r.get("id")) for r in res if r.get("id")]

    def pages_for(self, issue: Issue, limit: int = 5) -> list[dict]:
        ids: list[str] = []
        for rl in issue.remote_links:                       # pages linked from the story come first
            m = PAGE_ID.search(rl.get("url", ""))
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
        words = " ".join(w for w in re.findall(r"[A-Za-z]{4,}", issue.title)[:6])
        space = (" AND space in (" + ",".join(f'"{sp}"' for sp in self.spaces) + ")") if self.spaces else ""
        for cql in (f'type = page{space} AND text ~ "{issue.key}"',
                    f'type = page{space} AND title ~ "{words}"' if words else None):
            if not cql or len(ids) >= limit:
                continue
            try:
                for pid in self._search(cql, limit):
                    if pid not in ids:
                        ids.append(pid)
            except IntegrationError:
                pass
        pages = []
        for pid in ids[:limit]:
            try:
                pages.append(self.page(pid))
            except IntegrationError:
                continue
        return pages
