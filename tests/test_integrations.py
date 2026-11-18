"""Requirements → test design → run → write-back, against fake Jira / Confluence / GitHub / Zephyr Scale / Xray.

No AI anywhere: the story's criteria and the form in the pull request are enough to write the cases."""
import os
import subprocess
import time

import httpx
import pytest

from aiauto.integrations import IntegrationError, context, design, make
from aiauto.integrations.base import find_section, sections, wiki_to_text

from . import fake_tools as ft
from .conftest import EXAMPLES, _serve
from .test_agent import _start_hub  # noqa: E402


@pytest.fixture(scope="module")
def tools():
    srv, url, state = ft.serve()
    yield url, state
    srv.should_exit = True


@pytest.fixture
def fresh(tools):
    tools[1].reset()
    return tools


def _jira(url, **kw):
    return make("jira", {"base_url": url + "/site", "deployment": "cloud", "email": ft.EMAIL, "project_key": "SHOP", **kw},
                {"token": ft.JIRA_TOKEN})


def _pack(url, key="SHOP-1"):
    conf = make("confluence", {"base_url": url + "/site", "deployment": "cloud", "email": ft.EMAIL}, {"token": ft.JIRA_TOKEN})
    gh = make("github", {"base_url": url + "/gh", "repos": "acme/shop"}, {"token": ft.GH_TOKEN})
    return context.build(_jira(url), key, [conf], [gh])


# ------------------------------------------------------------------------------- pieces
def test_wiki_sections_and_whole_word_headings():
    text = wiki_to_text("h2. Acceptance criteria\n# one\n# two\nh3. Actual result\nbroken")
    secs = sections(text)
    assert [x.split(". ", 1)[1] for x in find_section(secs, "acceptance")] == ["one", "two"]
    assert find_section(secs, "ac") == []           # 'ac' must not match 'actual'


def test_tokens_are_required_and_checked(fresh):
    url, state = fresh
    with pytest.raises(IntegrationError, match="e-mail"):
        make("jira", {"base_url": url + "/site", "deployment": "cloud"}, {"token": "x"})
    with pytest.raises(IntegrationError, match="token"):
        make("github", {}, {})
    bad = make("jira", {"base_url": url + "/site", "deployment": "cloud", "email": ft.EMAIL}, {"token": "wrong"})
    with pytest.raises(IntegrationError) as e:
        bad.test()
    assert e.value.status == 401 and state.auth_failures == 1
    assert "QA Bot" in _jira(url).test()


def test_context_pack_brings_story_docs_and_code_together(fresh):
    url, _ = fresh
    pack = _pack(url)
    issue = pack["issue"]
    assert issue["title"] == "Sign up form" and len(issue["acceptance"]) == 5
    assert [d["title"] for d in pack["docs"]] == ["Sign up design"]
    fields = {f["name"]: f for f in pack["code"]["fields"]}
    assert fields["Username"]["maxlength"] == "20" and fields["Age"]["max"] == "99" and fields["Password"]["minlength"] == "8"
    assert pack["code"]["buttons"] == ["Create account"] and pack["code"]["pages"] == ["signup.html"]
    assert pack["fingerprint"] == context.fingerprint(pack)


def test_rules_design_positive_negative_and_boundary_cases(fresh):
    url, _ = fresh
    out = design.design(_pack(url))
    by = {c["title"].split(" · ", 1)[1]: c for c in out["cases"]}
    assert {c["type"] for c in out["cases"]} == {"positive", "negative", "boundary"}
    assert all(not c["notes"] for c in out["cases"]), [c["notes"] for c in out["cases"] if c["notes"]]
    happy = by["Works when I fill valid details and click Create account"]
    assert happy["steps"][0] == "Open ${base_url}/signup.html" and happy["steps"][-1] == 'Verify page contains "Account created"'
    assert "Type ${password} into Password" in happy["steps"] and happy["sensitive"] == ["password"]
    assert 'Verify page contains "Age must be 18-99"' in by["Rejects Age = 100 (above the maximum)"]["steps"]
    assert by["Rejects an invalid Email"]["steps"][1] == 'Type "not-an-email" into Email'
    assert 'Verify page contains "Enter a valid email"' in by["Rejects an invalid Email"]["steps"]
    # that message belongs to the format rule: an empty e-mail only checks the form isn't accepted
    assert by["Rejects an empty Email"]["steps"][-1] == 'Verify page does not contain "Account created"'
    assert not any("Enter a valid email" in s for s in by["Rejects an empty Email"]["steps"])
    # the page caps Username itself (maxlength=20): the case checks the field keeps 20, not an error message
    assert any(s.startswith("Verify the Username field does not contain") for s in by["Keeps Username to 20 characters"]["steps"])
    assert all(row["missing"] == [] for row in out["coverage"][1:])


def test_bug_becomes_a_regression_case(fresh):
    url, _ = fresh
    cases = design.design(_pack(url, "SHOP-2"))["cases"]
    reg = next(c for c in cases if c["type"] == "regression")
    assert reg["steps"] == ['Open ${base_url}/signup.html', 'Type "ab" into Password', 'Click Create account',
                            'Verify page contains "Password too short"', 'Verify page does not contain "Account created"']


def test_xray_api_key_is_exchanged_and_results_imported(fresh):
    url, state = fresh
    x = make("xray", {"base_url": url + "/xray", "project_key": "SHOP"}, {"client_id": ft.XRAY_ID, "client_secret": ft.XRAY_SECRET})
    assert "accepted" in x.test()
    issue = context.issue_of(_pack(url))
    case = design.design(_pack(url))["cases"][0]
    key = x.record_execution("Autopilot run", case, {"status": "passed", "comment": "ok", "steps": [{"status": "passed"}]}, issue)
    body = state.xray_imports[0]
    assert key == "SHOP-X1" and body["tests"][0]["status"] == "PASSED"
    assert body["tests"][0]["testInfo"]["requirementKeys"] == ["SHOP-1"]


# ------------------------------------------------------------------------------- the whole flow through the hub
@pytest.fixture
def hub(tmp_path):
    procs = []
    env = {**os.environ, "AIAUTO_AUTH": "off", "AIAUTO_DISABLE_LLM": "1", "AIAUTO_PUBLIC_URL": "https://autopilot.example",
           "AIAUTO_DB": str(tmp_path / "hub.db"), "AIAUTO_ARTIFACTS": str(tmp_path / "artifacts"), "ANTHROPIC_API_KEY": "",
           "PYTHONPATH": os.pathsep.join([str(EXAMPLES.parent), os.environ.get("PYTHONPATH", "")])}
    base = _start_hub(tmp_path, procs, env)
    yield base
    for p in procs:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


@pytest.fixture(scope="module")
def signup_url():
    srv, url = _serve(EXAMPLES / "signup")
    yield url
    srv.shutdown()


def _wait(fn, timeout=240, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(every)
    raise AssertionError("timed out")


def test_story_to_cases_to_runs_to_jira_and_zephyr(fresh, hub, signup_url):
    url, state = fresh
    c = httpx.Client(base_url=hub, timeout=60, headers={"X-Requested-With": "autopilot"})
    conns = {
        "jira": ({"base_url": url + "/site", "deployment": "cloud", "email": ft.EMAIL, "project_key": "SHOP",
                  "pass_transition": "QA Passed", "fail_transition": "In Progress", "create_bugs": "yes"}, {"token": ft.JIRA_TOKEN}),
        "confluence": ({"base_url": url + "/site", "deployment": "cloud", "email": ft.EMAIL}, {"token": ft.JIRA_TOKEN}),
        "github": ({"base_url": url + "/gh", "repos": "acme/shop"}, {"token": ft.GH_TOKEN}),
        "zephyr_scale": ({"base_url": url + "/zephyr/v2", "project_key": "SHOP", "folder": "Autopilot"}, {"token": ft.ZEPHYR_TOKEN}),
    }
    for kind, (settings, secrets) in conns.items():
        r = c.put(f"/api/connections/{kind}", json={"settings": settings, "secrets": secrets})
        assert r.status_code == 200, r.text
        assert r.json()["secrets"]["token"] == "••••••••"            # tokens never come back
        assert c.post(f"/api/connections/{kind}/test").json()["ok"], kind
    listed = c.get("/api/connections").json()
    assert ft.JIRA_TOKEN not in str(listed) and ft.GH_TOKEN not in str(listed)
    # saving again with the masked value keeps the stored token
    assert c.put("/api/connections/jira", json={"settings": conns["jira"][0], "secrets": {"token": "••••••••"}}).status_code == 200
    assert c.post("/api/connections/jira/test").json()["ok"]

    found = c.get("/api/requirements", params={"q": "sign up"}).json()
    assert [i["key"] for i in found][:1] == ["SHOP-1"]

    d = c.post("/api/designs", json={"issue_key": "SHOP-1"}).json()
    titles = {x["title"].split(" · ", 1)[1]: x["id"] for x in d["cases"]}
    pick = ["Works when I fill valid details and click Create account", "Rejects an invalid Email",
            "Rejects Age = 100 (above the maximum)", "Accepts Age = 99 (maximum)", "Rejects Password shorter than 8 characters",
            "Keeps Username to 20 characters"]
    r = c.post(f"/api/designs/{d['id']}/status", json={"case_ids": [titles[t] for t in pick], "status": "approved"}).json()
    assert r["blocked"] == [] and sum(x["status"] == "approved" for x in r["cases"]) == len(pick)

    run = c.post(f"/api/designs/{d['id']}/run", json={
        "base_url": signup_url, "config": {"record_video": False, "live_view": False, "highlight_ms": 0, "timeout_ms": 4000}})
    assert run.status_code == 200, run.text
    ids = run.json()["run_ids"]
    assert len(ids) == len(pick) and run.json()["skipped"] == []
    # Zephyr: cases were pushed before running, with their steps, into the folder, linked to the story
    assert len(state.cases) == len(pick) and all(x["steps"] for x in state.cases.values())
    assert all(x.get("folderId") == 55 for x in state.cases.values()) and {i for _, i in state.case_links} == {10001}

    def finished():
        rows = c.get(f"/api/designs/{d['id']}/runs").json()
        return rows if rows and all(x["status"] in ("passed", "failed") for x in rows) else None
    rows = _wait(finished)
    status = {next(t for t, i in titles.items() if i == x["case_id"]): x["status"] for x in rows}
    assert status.pop("Rejects Age = 100 (above the maximum)") == "failed"      # the page's deliberate bug
    errors = {x["test_name"]: x.get("error") for x in rows if x["status"] == "failed"}
    assert set(status.values()) == {"passed"}, errors

    _wait(lambda: state.transitions)            # the story update is the last write-back step
    assert len(state.executions) == len(pick) and len(state.cycles) == 1
    assert sorted(e["statusName"] for e in state.executions).count("Fail") == 1
    assert state.comments["SHOP-1"][0].startswith(f"Autopilot ran {len(pick)} test case(s) for SHOP-1: {len(pick) - 1} passed, 1 failed")
    assert "https://autopilot.example/artifacts/" in state.comments["SHOP-1"][0]
    assert state.transitions == [("SHOP-1", "In Progress")]
    (bug_key, bug), = state.bugs.items()
    assert "Age = 100" in bug["summary"] and any(lb.startswith("autopilot-") for lb in bug["labels"])
    assert state.links[0]["outwardIssue"]["key"] == "SHOP-1"
    assert state.attachments and state.attachments[0][0] == bug_key

    # the same failure again: no second bug, a comment on the first one
    again = c.post(f"/api/designs/{d['id']}/run", json={"base_url": signup_url, "case_ids": [titles["Rejects Age = 100 (above the maximum)"]],
                                                         "config": {"record_video": False, "live_view": False, "timeout_ms": 4000}}).json()
    _wait(lambda: len(state.comments.get(bug_key, [])) == 1 and len(state.comments["SHOP-1"]) == 2)
    assert len(state.bugs) == 1 and len(state.cycles) == 2 and again["run_ids"]
    log = c.get(f"/api/designs/{d['id']}/log").json()
    assert all(x["ok"] for x in log), [x for x in log if not x["ok"]]
    assert {"design", "push case", "start cycle", "record execution", "create bug", "bug (seen again)", "comment", "transition"} <= \
        {x["action"] for x in log}


# ------------------------------------------------------------------------------- the negative check on both engines
@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_text_absent_checks_on_both_engines(engine, tmp_path):
    from aiauto.models import RunConfig
    from aiauto.nlp.parser import parse_text
    from aiauto.runner import run_test
    page = (EXAMPLES / "signup" / "signup.html").as_uri()
    ok = f'''Open {page}
Type "abcdefghijabcdefghijk" into Username
Verify the Username field does not contain "abcdefghijabcdefghijk"
Click Create account
Verify page contains "Email is required"
Verify page does not contain "Account created"
I should not see "Age must be 18-99"'''
    test, info = parse_text(ok, {}, use_llm=False)
    assert not info["unparsed"] and sum(s.action.value == "assert_no_text" for s in test.steps) == 3
    cfg = RunConfig(engine=engine, timeout_ms=4000, record_video=False, live_view=False)
    s = run_test(test, cfg, tmp_path / "art")
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    bad, _ = parse_text(f'Open {page}\nClick Create account\nVerify page does not contain "Email is required"', {}, use_llm=False)
    s = run_test(bad, cfg, tmp_path / "art")
    assert s.status.value == "failed" and "NOT to contain" in s.results[-1].error


# ------------------------------------------------------------------------------- local work first, publish later
STORY_MD = """# Sign up form

As a visitor I want to create an account.

## Acceptance criteria
- Given I am on the sign up page, when I fill valid details and click Create account then I see "Account created"
- Email is required and must be a valid email address; shows "Enter a valid email"
- Age must be between 18 and 99; shows "Age must be 18-99"
"""


def _git_repo(tmp_path):
    repo = tmp_path / "shop"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "signup.html").write_text((EXAMPLES / "signup" / "signup.html").read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "web" / "about.html").write_text("<h1>About us</h1><button>Contact</button>", encoding="utf-8")
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(g + ["add", "web/about.html"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "about page"], check=True)
    subprocess.run(g + ["add", "web/signup.html"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "SHOP-1: sign up form"], check=True)
    return repo


def test_pasted_story_and_local_repo(tmp_path):
    repo = _git_repo(tmp_path)
    local = make("local_repo", {"path": str(repo)}, {})
    assert "SHOP-1: sign up form" in local.test()
    issue = context.issue_from_text(STORY_MD, key="shop-1")
    assert issue.key == "SHOP-1" and issue.title == "Sign up form" and len(issue.acceptance) == 3
    pack = context.build_for(issue, [], [local], notes="Usernames are unique.")
    assert [p["number"] for p in pack["code"]["prs"]] and pack["code"]["pages"] == ["signup.html"]   # found by the commit
    assert pack["docs"][0]["pasted"] and pack["meta"] == {"source": "pasted", "auto_sync": False}
    # no key: the UI file that shares the story's words
    pack2 = context.build_for(context.issue_from_text(STORY_MD), [], [local])
    assert pack2["code"]["pages"][0] == "signup.html" and "No commit mentions" in pack2["warnings"][0]
    cases = design.design(pack)["cases"]
    assert any(c["title"] == "SHOP-1 · Rejects Age = 100 (above the maximum)" for c in cases)
    with pytest.raises(IntegrationError, match="not a folder"):
        make("local_repo", {"path": str(tmp_path / "nope")}, {})


def test_local_first_then_publish(fresh, hub, signup_url, tmp_path):
    url, state = fresh
    repo = _git_repo(tmp_path)
    c = httpx.Client(base_url=hub, timeout=60, headers={"X-Requested-With": "autopilot"})
    assert c.put("/api/connections/local_repo", json={"settings": {"path": str(repo)}}).status_code == 200
    # no tracker, no test tool: paste the story, design, run — all local
    d = c.post("/api/designs", json={"story": {"text": STORY_MD}}).json()
    assert d["issue_key"] == "LOCAL" and d["auto_sync"] is False and not d["linked"]
    pick = [x["id"] for x in d["cases"] if "Age = 100" in x["title"] or "valid details" in x["title"]]
    c.post(f"/api/designs/{d['id']}/status", json={"case_ids": pick, "status": "approved"})
    r = c.post(f"/api/designs/{d['id']}/run", json={"base_url": signup_url, "config": {"record_video": False, "live_view": False}})
    assert r.status_code == 200, r.text
    _wait(lambda: c.get(f"/api/designs/{d['id']}").json()["unpublished"] == 2)
    assert not state.cases and not state.comments          # nothing left the hub

    # the tools are connected later: link the pasted story to its Jira key, then Publish
    c.put("/api/connections/jira", json={"settings": {"base_url": url + "/site", "deployment": "cloud", "email": ft.EMAIL,
                                                      "project_key": "SHOP"}, "secrets": {"token": ft.JIRA_TOKEN}})
    c.put("/api/connections/zephyr_scale", json={"settings": {"base_url": url + "/zephyr/v2", "project_key": "SHOP"},
                                                 "secrets": {"token": ft.ZEPHYR_TOKEN}})
    linked = c.post(f"/api/designs/{d['id']}/link", json={"issue_key": "SHOP-1"}).json()
    assert linked["issue_key"] == "SHOP-1" and linked["linked"] and all(x["title"].startswith("SHOP-1 · ") for x in linked["cases"])
    pub = c.post(f"/api/designs/{d['id']}/publish")
    assert pub.status_code == 200, pub.text
    assert pub.json()["results"] == 2 and pub.json()["design"]["unpublished"] == 0
    assert len(state.cases) == 2 and {i for _, i in state.case_links} == {10001}
    assert sorted(e["statusName"] for e in state.executions) == ["Fail", "Pass"]
    assert state.comments["SHOP-1"][0].startswith("Autopilot ran 2 test case(s) for SHOP-1: 1 passed, 1 failed")
    assert len(state.bugs) == 1
