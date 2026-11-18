"""Xray Cloud with an API key (client id + client secret, made in Xray Global Settings > API Keys). The key is
exchanged for a token that lasts 24 hours; we renew it automatically.

Each launch is one Test Execution (made through Xray's GraphQL API). Results go in with Xray's JSON import: the first
import of a case creates its Xray test (steps, linked to the story) with the label autopilot-case-<id>; that label is
how the test is found again, so later runs update the same test instead of creating new ones."""
from __future__ import annotations

import re
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

    # ---------------------------------------------------------------- GraphQL
    def _gql(self, query: str, variables: dict) -> dict:
        r = self.http.json("POST", "/api/v2/graphql", json={"query": query, "variables": variables},
                           headers={"Authorization": f"Bearer {self._token()}"}) or {}
        if r.get("errors"):
            raise IntegrationError("Xray: " + "; ".join(str(e.get("message", e)) for e in r["errors"])[:300])
        return r.get("data") or {}

    @staticmethod
    def label(case: dict) -> str:
        return "autopilot-case-" + re.sub(r"[^A-Za-z0-9_-]", "", case["id"])

    def find_test(self, case: dict) -> str:
        """The Xray test made for this case earlier (by its label), or ''."""
        q = "query($jql: String!) { getTests(jql: $jql, limit: 1) { results { issueId jira(fields: [\"key\"]) } } }"
        got = self._gql(q, {"jql": f'project = "{self.project}" AND labels = "{self.label(case)}"'})
        res = ((got.get("getTests") or {}).get("results") or [])
        return ((res[0].get("jira") or {}).get("key") or "") if res else ""

    # ---------------------------------------------------------------- tests tool
    def push_case(self, case: dict, issue: Issue) -> str:
        # an Xray test is created by the first results import (testInfo); pushing finds one made earlier
        return (case.get("external") or {}).get(self.kind) or self.find_test(case)

    def start_cycle(self, name: str, issue: Issue) -> str:
        q = ("mutation($jira: JSON!) { createTestExecution(jira: $jira) { testExecution { issueId jira(fields: [\"key\"]) } "
             "warnings } }")
        got = self._gql(q, {"jira": {"fields": {"summary": name[:250], "project": {"key": self.project}}}})
        te = (got.get("createTestExecution") or {}).get("testExecution") or {}
        key = (te.get("jira") or {}).get("key")
        if not key:
            raise IntegrationError("Xray: the Test Execution was not created")
        return key

    def record_execution(self, cycle: str, case: dict, result: dict, issue: Issue) -> str:
        test = {"status": STATUS.get(result["status"], "FAILED"), "comment": result.get("comment", "")[:2000],
                "steps": [{"status": STATUS.get(s["status"], "TODO"), "actualResult": (s.get("error") or "")[:1000]}
                          for s in result.get("steps") or []]}
        ext = case.setdefault("external", {})
        key = ext.get(self.kind) or self.find_test(case)
        if key:
            test["testKey"] = key
        else:
            info = {"summary": case["title"][:250], "type": "Manual", "projectKey": self.project, "labels": ["autopilot", self.label(case)],
                    "steps": [{"action": s, "data": "", "result": s if s.lower().startswith("verify") else ""} for s in case.get("steps") or []]}
            if issue.source != "pasted" and issue.key:
                info["requirementKeys"] = [issue.key]
            test["testInfo"] = info
        body = {"tests": [test]}
        if cycle:
            body["testExecutionKey"] = cycle
        else:
            body["info"] = {"project": self.project, "summary": f"Autopilot · {issue.key}"}
        r = self.http.post("/api/v2/import/execution", json=body, headers={"Authorization": f"Bearer {self._token()}"})
        if not key:
            ext[self.kind] = self.find_test(case)     # remember the test the import just created
        elif not ext.get(self.kind):
            ext[self.kind] = key
        return (r.json() or {}).get("key", "") or cycle
