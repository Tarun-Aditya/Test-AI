"""Stand-ins for Jira + Confluence (/site), GitHub (/gh), Zephyr Scale (/zephyr/v2) and Xray (/xray) that speak the
same REST shapes the connectors use, check the tokens, and record every write so tests can assert on them.

Run standalone to try the flow by hand:  python -m tests.fake_tools 8765"""
from __future__ import annotations

import base64
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

SIGNUP = (Path(__file__).resolve().parent.parent / "examples" / "signup" / "signup.html").read_text(encoding="utf-8")
EMAIL, JIRA_TOKEN, GH_TOKEN, ZEPHYR_TOKEN = "qa@example.com", "jira-api-token", "github_pat_fake", "zephyr-token"
XRAY_ID, XRAY_SECRET = "xray-client", "xray-secret"

STORY = """h2. Story
As a visitor I want to create an account so that I can order.

h2. Acceptance criteria
# Given I am on the sign up page, when I fill valid details and click Create account then I see "Account created"
# Email is required and must be a valid email address; shows "Enter a valid email"
# Username is required and must be at most 20 characters
# Age must be between 18 and 99; shows "Age must be 18-99"
# Password must be at least 8 characters; shows "Password too short"

h2. Notes
See the design page in Confluence."""

BUG = """h3. Steps to reproduce
# Open ${base_url}/signup.html
# Type "ab" into Password
# Click Create account

h3. Expected result
The page shows "Password too short"

h3. Actual result
The account is created and the page shows "Account created\""""


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.comments: dict[str, list[str]] = {}
        self.transitions: list[tuple[str, str]] = []
        self.status = {"SHOP-1": "In QA", "SHOP-2": "Open"}
        self.bugs: dict[str, dict] = {}
        self.links: list[dict] = []
        self.attachments: list[tuple[str, str, int]] = []
        self.cases: dict[str, dict] = {}
        self.case_links: list[tuple[str, int]] = []
        self.cycles: dict[str, dict] = {}
        self.executions: list[dict] = []
        self.xray_imports: list[dict] = []
        self.auth_failures = 0
        self.rate_limited_once = False


def make_app(state: State) -> FastAPI:
    app = FastAPI()

    def basic_ok(request: Request):
        want = "Basic " + base64.b64encode(f"{EMAIL}:{JIRA_TOKEN}".encode()).decode()
        if request.headers.get("authorization") != want:
            state.auth_failures += 1
            raise HTTPException(401, "Unauthorized")

    def bearer_ok(request: Request, token: str):
        if request.headers.get("authorization") != f"Bearer {token}":
            state.auth_failures += 1
            raise HTTPException(401, "Bad credentials")

    # ------------------------------------------------------------ Jira
    issues = {
        "SHOP-1": {"id": "10001", "summary": "Sign up form", "type": "Story", "description": STORY},
        "SHOP-2": {"id": "10002", "summary": "Short passwords are accepted on sign up", "type": "Bug", "description": BUG},
    }

    def brief(key):
        i = issues.get(key) or state.bugs.get(key)
        return {"key": key, "id": i["id"], "fields": {"summary": i["summary"], "status": {"name": state.status.get(key, "Open")},
                                                      "issuetype": {"name": i["type"]}, "labels": i.get("labels", [])}}

    @app.get("/site/rest/api/2/myself")
    def myself(request: Request):
        basic_ok(request)
        return {"displayName": "QA Bot", "emailAddress": EMAIL}

    @app.get("/site/rest/api/2/project/{key}")
    def project(key: str, request: Request):
        basic_ok(request)
        if key != "SHOP":
            raise HTTPException(404, "No project")
        return {"key": "SHOP", "name": "Shop"}

    @app.get("/site/rest/api/3/search/jql")
    def search(request: Request, jql: str, maxResults: int = 25):
        basic_ok(request)
        if 'labels = "' in jql:
            label = jql.split('labels = "')[1].split('"')[0]
            hits = [k for k, b in state.bugs.items() if label in b["labels"] and b["status"] != "Done"]
        elif "key = " in jql:
            k = jql.split("key = ")[1].split()[0]
            hits = [k] if k in issues else []
        else:
            text = jql.split('text ~ "')[1].split('"')[0].lower() if 'text ~ "' in jql else ""
            hits = [k for k, i in issues.items() if not text or text in (i["summary"] + i["description"]).lower()]
        return {"issues": [brief(k) for k in hits[:maxResults]]}

    @app.get("/site/rest/api/2/issue/{key}")
    def issue(key: str, request: Request):
        basic_ok(request)
        i = issues.get(key)
        if not i:
            raise HTTPException(404, "Issue does not exist")
        return {"key": key, "id": i["id"], "names": {"summary": "Summary", "customfield_10045": "Acceptance Criteria"},
                "fields": {"summary": i["summary"], "status": {"name": state.status.get(key, "Open")}, "issuetype": {"name": i["type"]},
                           "description": i["description"], "labels": ["signup"], "components": [{"name": "Web"}],
                           "issuelinks": [], "subtasks": [], "comment": {"comments": [{"body": "Design is final, see the wiki."}]},
                           "attachment": [], "customfield_10045": None}}

    @app.get("/site/rest/api/2/issue/{key}/remotelink")
    def remotelinks(key: str, request: Request):
        basic_ok(request)
        return [{"object": {"url": f"{str(request.base_url).rstrip('/')}/site/wiki/spaces/SHOP/pages/123/Signup", "title": "Sign up design"}}] \
            if key == "SHOP-1" else []

    @app.post("/site/rest/api/2/issue/{key}/comment")
    async def comment(key: str, request: Request):
        basic_ok(request)
        if not state.rate_limited_once:          # the first write gets a 429: the client must back off and retry
            state.rate_limited_once = True
            return JSONResponse({"message": "Rate limited"}, status_code=429, headers={"Retry-After": "0"})
        state.comments.setdefault(key, []).append((await request.json())["body"])
        return JSONResponse({"id": str(len(state.comments[key]))}, status_code=201)

    @app.get("/site/rest/api/2/issue/{key}/transitions")
    def transitions(key: str, request: Request):
        basic_ok(request)
        return {"transitions": [{"id": "31", "name": "Passed QA", "to": {"name": "QA Passed"}},
                                {"id": "41", "name": "Back to dev", "to": {"name": "In Progress"}}]}

    @app.post("/site/rest/api/2/issue/{key}/transitions")
    async def do_transition(key: str, request: Request):
        basic_ok(request)
        tid = (await request.json())["transition"]["id"]
        to = {"31": "QA Passed", "41": "In Progress"}[tid]
        state.status[key] = to
        state.transitions.append((key, to))
        return PlainTextResponse("", status_code=204)

    @app.post("/site/rest/api/2/issue")
    async def create_issue(request: Request):
        basic_ok(request)
        f = (await request.json())["fields"]
        key = f"SHOP-{100 + len(state.bugs)}"
        state.bugs[key] = {"id": str(20000 + len(state.bugs)), "summary": f["summary"], "type": f["issuetype"]["name"],
                           "description": f["description"], "labels": f.get("labels", []), "status": "Open"}
        return JSONResponse({"key": key, "id": state.bugs[key]["id"]}, status_code=201)

    @app.post("/site/rest/api/2/issueLink")
    async def link(request: Request):
        basic_ok(request)
        state.links.append(await request.json())
        return PlainTextResponse("", status_code=201)

    @app.post("/site/rest/api/2/issue/{key}/attachments")
    async def attach(key: str, request: Request):
        basic_ok(request)
        if request.headers.get("x-atlassian-token") != "no-check":
            raise HTTPException(403, "XSRF check failed")
        form = await request.form()
        f = form["file"]
        state.attachments.append((key, f.filename, len(await f.read())))
        return [{"filename": f.filename}]

    # ------------------------------------------------------------ Confluence
    @app.get("/site/wiki/rest/api/user/current")
    def wiki_me(request: Request):
        basic_ok(request)
        return {"displayName": "QA Bot"}

    @app.get("/site/wiki/api/v2/pages/{pid}")
    def page(pid: str, request: Request):
        basic_ok(request)
        if pid != "123":
            raise HTTPException(404, "No page")
        return {"id": "123", "title": "Sign up design", "_links": {"webui": "/spaces/SHOP/pages/123"},
                "body": {"storage": {"value": "<h2>Sign up</h2><p>Usernames are unique. After sign up the user sees "
                                              "<strong>Account created</strong>.</p><ul><li>Age limit 18 to 99</li></ul>"}}}

    @app.get("/site/wiki/rest/api/search")
    def wiki_search(request: Request, cql: str, limit: int = 5):
        basic_ok(request)
        return {"results": [{"content": {"id": "123"}}] if "SHOP-1" in cql or "Sign" in cql else []}

    # ------------------------------------------------------------ GitHub
    @app.get("/gh/user")
    def gh_user(request: Request):
        bearer_ok(request, GH_TOKEN)
        return {"login": "qa-bot"}

    @app.get("/gh/repos/acme/shop")
    def gh_repo(request: Request):
        bearer_ok(request, GH_TOKEN)
        return {"full_name": "acme/shop"}

    @app.get("/gh/search/issues")
    def gh_search(request: Request, q: str):
        bearer_ok(request, GH_TOKEN)
        return {"items": [{"number": 7}] if '"SHOP-1"' in q else []}

    @app.get("/gh/repos/acme/shop/pulls")
    def gh_pulls(request: Request):
        bearer_ok(request, GH_TOKEN)
        return [{"number": 7, "title": "SHOP-1 sign up form", "body": "", "head": {"ref": "feature/SHOP-1"}}]

    @app.get("/gh/repos/acme/shop/pulls/7")
    def gh_pr(request: Request):
        bearer_ok(request, GH_TOKEN)
        return {"number": 7, "title": "SHOP-1: sign up form", "html_url": "https://github.example/acme/shop/pull/7", "head": {"sha": "abc123"}}

    @app.get("/gh/repos/acme/shop/pulls/7/files")
    def gh_files(request: Request):
        bearer_ok(request, GH_TOKEN)
        patch = "\n".join("+" + ln for ln in SIGNUP.splitlines()[:40])
        return [{"filename": "web/signup.html", "status": "added", "patch": patch}]

    @app.get("/gh/repos/acme/shop/contents/{path:path}")
    def gh_contents(path: str, request: Request, ref: str = ""):
        bearer_ok(request, GH_TOKEN)
        if path != "web/signup.html":
            raise HTTPException(404, "Not Found")
        return {"encoding": "base64", "content": base64.b64encode(SIGNUP.encode()).decode()}

    # ------------------------------------------------------------ Zephyr Scale
    @app.get("/zephyr/v2/projects/{key}")
    def z_project(key: str, request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        return {"key": key, "id": 1}

    @app.get("/zephyr/v2/folders")
    def z_folders(request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        return {"values": []}

    @app.post("/zephyr/v2/folders")
    async def z_folder(request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        return JSONResponse({"id": 55, **(await request.json())}, status_code=201)

    @app.post("/zephyr/v2/testcases")
    async def z_case(request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        body = await request.json()
        key = f"SHOP-T{len(state.cases) + 1}"
        state.cases[key] = {**body, "steps": []}
        return JSONResponse({"key": key, "id": len(state.cases)}, status_code=201)

    @app.get("/zephyr/v2/testcases/{key}")
    def z_get_case(key: str, request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        return {k: v for k, v in state.cases[key].items() if k != "steps"} | {"key": key}

    @app.put("/zephyr/v2/testcases/{key}")
    async def z_put_case(key: str, request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        state.cases[key].update(await request.json())
        return PlainTextResponse("", status_code=200)

    @app.post("/zephyr/v2/testcases/{key}/links/issues")
    async def z_link(key: str, request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        state.case_links.append((key, (await request.json())["issueId"]))
        return JSONResponse({"id": 1}, status_code=201)

    @app.post("/zephyr/v2/testcases/{key}/teststeps")
    async def z_steps(key: str, request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        body = await request.json()
        assert body["mode"] == "OVERWRITE"
        state.cases[key]["steps"] = [i["inline"]["description"] for i in body["items"]]
        return JSONResponse({"id": 1}, status_code=201)

    @app.post("/zephyr/v2/testcycles")
    async def z_cycle(request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        key = f"SHOP-R{len(state.cycles) + 1}"
        state.cycles[key] = await request.json()
        return JSONResponse({"key": key, "id": len(state.cycles)}, status_code=201)

    @app.post("/zephyr/v2/testcycles/{key}/links/issues")
    async def z_cycle_link(key: str, request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        return JSONResponse({"id": 1}, status_code=201)

    @app.post("/zephyr/v2/testexecutions")
    async def z_exec(request: Request):
        bearer_ok(request, ZEPHYR_TOKEN)
        body = await request.json()
        if body["testCaseKey"] not in state.cases or body["testCycleKey"] not in state.cycles:
            raise HTTPException(400, "unknown test case or cycle")
        state.executions.append(body)
        return JSONResponse({"key": f"SHOP-E{len(state.executions)}", "id": len(state.executions)}, status_code=201)

    # ------------------------------------------------------------ Xray Cloud
    @app.post("/xray/api/v2/authenticate")
    async def x_auth(request: Request):
        body = await request.json()
        if body != {"client_id": XRAY_ID, "client_secret": XRAY_SECRET}:
            raise HTTPException(401, "bad key")
        return JSONResponse('"xray-jwt-token"', media_type="application/json")

    @app.post("/xray/api/v2/import/execution")
    async def x_import(request: Request):
        bearer_ok(request, "xray-jwt-token")
        body = await request.json()
        state.xray_imports.append(body)
        return {"id": "1", "key": f"SHOP-X{len(state.xray_imports)}"}

    return app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int | None = None) -> tuple[uvicorn.Server, str, State]:
    state = State()
    port = port or _free_port()
    srv = uvicorn.Server(uvicorn.Config(make_app(state), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    return srv, f"http://127.0.0.1:{port}", state


if __name__ == "__main__":
    srv, url, _ = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print(f"fake Jira/Confluence: {url}/site   GitHub: {url}/gh   Zephyr Scale: {url}/zephyr/v2   Xray: {url}/xray")
    print(f"Jira e-mail {EMAIL} token {JIRA_TOKEN} | GitHub token {GH_TOKEN} | Zephyr token {ZEPHYR_TOKEN} | Xray {XRAY_ID}/{XRAY_SECRET}")
    threading.Event().wait()
