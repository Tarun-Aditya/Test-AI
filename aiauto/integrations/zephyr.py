"""Zephyr Scale (Cloud) with its API access token (Bearer, made in Jira: Apps > Zephyr API Access Tokens).
Writes test cases with steps linked to the story, test cycles, and executions with step results.

Endpoint paths follow Zephyr Scale's public REST API v2; check them against your instance in the spike
(the reference only renders in a browser). They are in one place here so a correction is one edit."""
from __future__ import annotations

from .base import Connector, Http, IntegrationError, Issue, bearer

STATUS = {"passed": "Pass", "failed": "Fail", "cancelled": "Not Executed", "interrupted": "Blocked", "skipped": "Not Executed"}


class ZephyrScale(Connector):
    kind, label, roles, status = "zephyr_scale", "Zephyr Scale", ("tests",), "standard"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        token = self.secrets.get("token") or ""
        if not token:
            raise IntegrationError("Zephyr Scale: add an API access token (Jira > Apps > Zephyr API Access Tokens)")
        self.project = (self.s.get("project_key") or "").strip()
        if not self.project:
            raise IntegrationError("Zephyr Scale: set the Jira project key")
        self.http = Http(self.s.get("base_url") or "https://api.zephyrscale.smartbear.com/v2", bearer(token), "Zephyr Scale")
        self.status_names = {**STATUS, **(self.s.get("status_names") or {})}
        self._folder: int | None = None

    def test(self) -> str:
        p = self.http.json("GET", f"/projects/{self.project}")
        return f"project {p.get('key', self.project)} reachable"

    def _folder_id(self) -> int | None:
        name = (self.s.get("folder") or "").strip()
        if not name:
            return None
        if self._folder is None:
            got = self.http.json("GET", "/folders", params={"projectKey": self.project, "folderType": "TEST_CASE", "maxResults": 200})
            match = next((f for f in (got or {}).get("values", []) if f.get("name") == name), None)
            if not match:
                match = self.http.json("POST", "/folders", json={"projectKey": self.project, "name": name,
                                                                 "folderType": "TEST_CASE", "parentId": None})
            self._folder = match.get("id")
        return self._folder

    def push_case(self, case: dict, issue: Issue) -> str:
        key = (case.get("external") or {}).get(self.kind)
        body = {"projectKey": self.project, "name": case["title"][:255], "objective": case.get("objective") or "",
                "precondition": "\n".join(case.get("preconditions") or []),
                "labels": ["autopilot", case.get("type", "positive")], "priorityName": case.get("priority") or "Normal"}
        if self._folder_id():
            body["folderId"] = self._folder_id()
        if key:
            cur = self.http.json("GET", f"/testcases/{key}")
            cur.update({k: v for k, v in body.items() if k != "projectKey"})
            self.http.put(f"/testcases/{key}", json=cur)
        else:
            key = self.http.json("POST", "/testcases", json=body)["key"]
            if issue.id:
                self.http.post(f"/testcases/{key}/links/issues", json={"issueId": int(issue.id)})
        steps = [{"inline": {"description": s, "testData": "", "expectedResult": s if s.lower().startswith(("verify", "check"))
                             else ""}} for s in case.get("steps") or []]
        if steps:
            self.http.post(f"/testcases/{key}/teststeps", json={"mode": "OVERWRITE", "items": steps})
        return key

    def start_cycle(self, name: str, issue: Issue) -> str:
        cyc = self.http.json("POST", "/testcycles", json={"projectKey": self.project, "name": name[:255],
                                                          "description": f"Autopilot runs for {issue.key}: {issue.title}"})
        if issue.id:
            try:
                self.http.post(f"/testcycles/{cyc['key']}/links/issues", json={"issueId": int(issue.id)})
            except IntegrationError:
                pass
        return cyc["key"]

    def record_execution(self, cycle: str, case: dict, result: dict, issue: Issue) -> str:
        key = (case.get("external") or {}).get(self.kind)
        if not key:
            raise IntegrationError("Zephyr Scale: push the test case before recording its run")
        st = self.status_names
        body = {"projectKey": self.project, "testCaseKey": key, "testCycleKey": cycle,
                "statusName": st.get(result["status"], "Fail"), "executionTime": int(result.get("duration_ms") or 0),
                "comment": result.get("comment", "")[:2000],
                "testScriptResults": [{"statusName": st.get(s["status"], "Not Executed"),
                                       "actualResult": (s.get("error") or "")[:1000]} for s in result.get("steps") or []]}
        if self.s.get("environment"):
            body["environmentName"] = self.s["environment"]
        got = self.http.json("POST", "/testexecutions", json=body) or {}
        return got.get("key") or str(got.get("id", ""))
