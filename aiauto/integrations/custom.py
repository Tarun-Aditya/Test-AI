"""Any in-house tool over plain JSON: a URL to read stories from (optional) and a URL that receives results.
An optional token is sent as a Bearer header (or under a header name you choose)."""
from __future__ import annotations

from .base import Connector, Http, IntegrationError, Issue


class Custom(Connector):
    kind, label, roles, status = "custom", "Custom tool (JSON)", ("tracker", "tests"), "under development"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        self.issues_url = (self.s.get("issues_url") or "").rstrip("/")
        self.results_url = self.s.get("results_url") or ""
        if not (self.issues_url or self.results_url):
            raise IntegrationError("Custom tool: set a stories URL, a results URL, or both")
        tok = self.secrets.get("token")
        hdr = {(self.s.get("header") or "Authorization"): (f"Bearer {tok}" if (self.s.get("header") or "Authorization") == "Authorization" else tok)} if tok else {}
        self.http = Http("", hdr, "Custom tool")

    def test(self) -> str:
        if self.issues_url:
            self.http.get(self.issues_url, params={"q": ""})
        return "reachable"

    def _issue(self, d: dict) -> Issue:
        return Issue(key=str(d.get("key") or d.get("id")), id=str(d.get("id") or d.get("key")), title=d.get("title") or d.get("summary") or "",
                     type=d.get("type", ""), status=d.get("status", ""), url=d.get("url", ""), description=d.get("description", ""),
                     acceptance=list(d.get("acceptance") or d.get("acceptance_criteria") or []),
                     steps_to_reproduce=list(d.get("steps_to_reproduce") or []), expected_result=d.get("expected_result", ""),
                     labels=list(d.get("labels") or []), source="custom")

    def search(self, query: str, limit: int = 25) -> list[Issue]:
        if not self.issues_url:
            return []
        return [self._issue(d) for d in (self.http.json("GET", self.issues_url, params={"q": query}) or [])[:limit]]

    def get_issue(self, key: str) -> Issue:
        return self._issue(self.http.json("GET", f"{self.issues_url}/{key}"))

    def comment(self, key: str, text: str) -> None:
        if self.results_url:
            self.http.post(self.results_url, json={"event": "comment", "story": key, "text": text})

    def push_case(self, case: dict, issue: Issue) -> str:
        if self.results_url:
            self.http.post(self.results_url, json={"event": "test_case", "story": issue.key, "case": case})
        return case["id"]

    def start_cycle(self, name: str, issue: Issue) -> str:
        return name

    def record_execution(self, cycle: str, case: dict, result: dict, issue: Issue) -> str:
        self.http.post(self.results_url, json={"event": "result", "story": issue.key, "cycle": cycle, "case": case["id"],
                                               "title": case["title"], **result})
        return case["id"]
