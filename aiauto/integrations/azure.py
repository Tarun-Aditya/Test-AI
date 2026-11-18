"""Azure DevOps with a personal access token (Basic, empty user name). Reads user stories / bugs (acceptance
criteria, repro steps, links), comments on them, and records automated test runs with one result per case."""
from __future__ import annotations

import re

from .base import Connector, Http, IntegrationError, Issue, basic, html_to_text, items

V = {"api-version": "7.1"}
OUTCOME = {"passed": "Passed", "failed": "Failed", "cancelled": "NotExecuted", "interrupted": "Aborted", "skipped": "NotExecuted"}


class AzureDevOps(Connector):
    kind, label, roles, status = "azure_devops", "Azure DevOps", ("tracker", "tests"), "under development"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        token = self.secrets.get("token") or ""
        org = (self.s.get("org_url") or "").rstrip("/")
        self.project = (self.s.get("project") or "").strip()
        if not (token and org and self.project):
            raise IntegrationError("Azure DevOps: set the organisation URL, the project and a personal access token")
        self.org = org
        self.http = Http(f"{org}/{self.project}", basic("", token), "Azure DevOps")

    def test(self) -> str:
        p = self.http.json("GET", f"{self.org}/_apis/projects/{self.project}", params=V)
        return f"project {p.get('name', self.project)} reachable"

    def _item(self, wi: dict) -> Issue:
        f = wi.get("fields") or {}
        desc = html_to_text(f.get("System.Description"))
        ac = items(html_to_text(f.get("Microsoft.VSTS.Common.AcceptanceCriteria")).splitlines())
        repro = items(html_to_text(f.get("Microsoft.VSTS.TCM.ReproSteps")).splitlines())
        links = []
        for rel in wi.get("relations") or []:
            m = re.search(r"/workItems/(\d+)$", rel.get("url", ""))
            if m:
                links.append({"key": m.group(1), "title": "", "relation": (rel.get("attributes") or {}).get("name") or rel.get("rel", "")})
        return Issue(key=str(wi["id"]), id=str(wi["id"]), title=f.get("System.Title", ""), type=f.get("System.WorkItemType", ""),
                     status=f.get("System.State", ""), url=f"{self.org}/{self.project}/_workitems/edit/{wi['id']}",
                     description=desc, acceptance=ac, steps_to_reproduce=repro,
                     labels=[t.strip() for t in (f.get("System.Tags") or "").split(";") if t.strip()], links=links, source="azure_devops")

    def search(self, query: str, limit: int = 25) -> list[Issue]:
        q = (query or "").replace("'", "''")
        where = f"[System.Id] = {int(q)}" if q.isdigit() else (f"[System.Title] CONTAINS '{q}'" if q else "[System.Id] > 0")
        wiql = {"query": f"SELECT [System.Id] FROM workitems WHERE [System.TeamProject] = @project AND {where} "
                         f"AND [System.WorkItemType] IN ('User Story','Bug','Product Backlog Item','Feature') ORDER BY [System.ChangedDate] DESC"}
        ids = [w["id"] for w in (self.http.json("POST", "/_apis/wit/wiql", params={**V, "$top": limit}, json=wiql) or {}).get("workItems", [])][:limit]
        if not ids:
            return []
        got = self.http.json("GET", "/_apis/wit/workitems", params={**V, "ids": ",".join(map(str, ids))})
        return [self._item(w) for w in (got or {}).get("value", [])]

    def get_issue(self, key: str) -> Issue:
        return self._item(self.http.json("GET", f"/_apis/wit/workitems/{key}", params={**V, "$expand": "Relations"}))

    def comment(self, key: str, text: str) -> None:
        self.http.post(f"/_apis/wit/workItems/{key}/comments", params={"api-version": "7.1-preview.4"}, json={"text": text})

    def transition(self, key: str, to_status: str) -> str:
        self.http.patch(f"/_apis/wit/workitems/{key}", params=V, headers={"Content-Type": "application/json-patch+json"},
                        json=[{"op": "add", "path": "/fields/System.State", "value": to_status}])
        return to_status

    def create_bug(self, summary: str, description: str, story: Issue, labels: list[str]) -> str:
        ops = [{"op": "add", "path": "/fields/System.Title", "value": summary[:250]},
               {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": description.replace("\n", "<br>")},
               {"op": "add", "path": "/fields/System.Tags", "value": "; ".join(labels)},
               {"op": "add", "path": "/relations/-", "value": {"rel": "System.LinkTypes.Related",
                                                                "url": f"{self.org}/_apis/wit/workItems/{story.id}"}}]
        bug = self.http.json("POST", "/_apis/wit/workitems/$Bug", params=V,
                             headers={"Content-Type": "application/json-patch+json"}, json=ops)
        return str(bug["id"])

    def find_bug(self, label: str) -> str | None:
        wiql = {"query": "SELECT [System.Id] FROM workitems WHERE [System.TeamProject] = @project AND [System.WorkItemType] = 'Bug' "
                         f"AND [System.Tags] CONTAINS '{label}' AND [System.State] NOT IN ('Closed', 'Done', 'Removed', 'Resolved')"}
        got = (self.http.json("POST", "/_apis/wit/wiql", params={**V, "$top": 1}, json=wiql) or {}).get("workItems") or []
        return str(got[0]["id"]) if got else None

    def attach(self, key: str, filename: str, data: bytes) -> None:
        up = self.http.json("POST", "/_apis/wit/attachments", params={**V, "fileName": filename},
                            headers={"Content-Type": "application/octet-stream"}, content=data)
        self.http.patch(f"/_apis/wit/workitems/{key}", params=V, headers={"Content-Type": "application/json-patch+json"},
                        json=[{"op": "add", "path": "/relations/-", "value": {"rel": "AttachedFile", "url": up["url"]}}])

    def push_case(self, case: dict, issue: Issue) -> str:
        return (case.get("external") or {}).get(self.kind) or ""     # results are recorded per run; cases live here

    def start_cycle(self, name: str, issue: Issue) -> str:
        run = self.http.json("POST", "/_apis/test/runs", params=V, json={"name": name[:255], "automated": True, "state": "InProgress"})
        return str(run["id"])

    def record_execution(self, cycle: str, case: dict, result: dict, issue: Issue) -> str:
        self.http.post(f"/_apis/test/Runs/{cycle}/results", params=V, json=[{
            "testCaseTitle": case["title"][:255], "automatedTestName": f"autopilot.{case['id']}", "outcome": OUTCOME.get(result["status"], "Failed"),
            "state": "Completed", "durationInMs": int(result.get("duration_ms") or 0),
            "errorMessage": ((result.get("failed") or {}).get("error") or "")[:1000], "comment": result.get("comment", "")[:1000]}])
        return cycle

    def finish_cycle(self, cycle: str) -> None:
        self.http.patch(f"/_apis/test/runs/{cycle}", params=V, json={"state": "Completed"})
