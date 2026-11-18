"""Xray Cloud with an API key (client id + client secret, made in Xray Global Settings > API Keys). The key is
exchanged for a token that lasts 24 hours; we renew it automatically. Results go in with Xray's JSON import, which
creates the Xray tests (with steps, linked to the story) the first time and updates their runs afterwards."""
from __future__ import annotations

import time

from .base import Connector, Http, IntegrationError, Issue

STATUS = {"passed": "PASSED", "failed": "FAILED", "cancelled": "TODO", "interrupted": "ABORTED", "skipped": "TODO"}
REGIONS = {"global": "https://xray.cloud.getxray.app", "us": "https://us.xray.cloud.getxray.app",
           "eu": "https://eu.xray.cloud.getxray.app", "au": "https://au.xray.cloud.getxray.app"}


class Xray(Connector):
    kind, label, roles, status = "xray", "Xray", ("tests",), "under development"
    _tokens: dict[str, tuple[float, str]] = {}

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        if not (self.secrets.get("client_id") and self.secrets.get("client_secret")):
            raise IntegrationError("Xray: add the API key's client id and client secret")
        self.project = (self.s.get("project_key") or "").strip()
        if not self.project:
            raise IntegrationError("Xray: set the Jira project key")
        self.base = (self.s.get("base_url") or REGIONS.get(self.s.get("region") or "global") or REGIONS["global"]).rstrip("/")
        self.http = Http(self.base, {}, "Xray")

    def _token(self) -> str:
        cid = self.secrets["client_id"]
        hit = self._tokens.get(cid)
        if hit and hit[0] > time.time():
            return hit[1]
        r = self.http.post("/api/v2/authenticate", json={"client_id": cid, "client_secret": self.secrets["client_secret"]})
        tok = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        tok = str(tok).strip().strip('"')
        self._tokens[cid] = (time.time() + 23 * 3600, tok)     # tokens last 24 h
        return tok

    def test(self) -> str:
        self._token()
        return f"API key accepted; project {self.project}"

    def push_case(self, case: dict, issue: Issue) -> str:
        # Xray tests are created by the first results import (testInfo), so pushing only marks the case ready
        return (case.get("external") or {}).get(self.kind) or ""

    def start_cycle(self, name: str, issue: Issue) -> str:
        return name                       # one Test Execution per import; the name becomes its summary

    def record_execution(self, cycle: str, case: dict, result: dict, issue: Issue) -> str:
        test = {"status": STATUS.get(result["status"], "FAILED"), "comment": result.get("comment", "")[:2000],
                "steps": [{"status": STATUS.get(s["status"], "TODO"), "actualResult": (s.get("error") or "")[:1000]}
                          for s in result.get("steps") or []]}
        key = (case.get("external") or {}).get(self.kind)
        if key:
            test["testKey"] = key
        else:
            test["testInfo"] = {"summary": case["title"][:250], "type": "Manual", "projectKey": self.project,
                                "requirementKeys": [issue.key],
                                "steps": [{"action": s, "data": "", "result": s if s.lower().startswith("verify") else ""}
                                          for s in case.get("steps") or []]}
        body = {"info": {"project": self.project, "summary": cycle[:250]}, "tests": [test]}
        r = self.http.post("/api/v2/import/execution", json=body, headers={"Authorization": f"Bearer {self._token()}"})
        return (r.json() or {}).get("key", "")
