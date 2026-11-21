"""Steps beyond clicking and typing: one-time passwords, API calls, network mocks, accessibility scan, visual baselines.
Run by the tool's own runner on both engines where it matters, and the scripts they leave run on their own."""
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from aiauto import script_runtime as sr
from aiauto.data import Masker, substitute
from aiauto.models import Action, RunConfig, Step, TestCase
from aiauto.nlp.parser import parse_text
from aiauto.runner import run_test

from .conftest import EXAMPLES

ITEMS = {"items": ["Apples", "Pears", "Plums"]}


class Handler(http.server.SimpleHTTPRequestHandler):
    posts = []

    def log_message(self, *a):
        pass

    def _json(self, code, doc):
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/items":
            return self._json(200, ITEMS)
        if self.path == "/api/health":
            return self._json(200, {"status": "ok", "data": {"token": "tok-123", "n": 7}})
        if self.path == "/api/gone":
            return self._json(404, {"error": "gone"})
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Handler.posts.append(json.loads(self.rfile.read(n) or b"{}"))
        return self._json(201, {"created": True})


@pytest.fixture(scope="module")
def site():
    import functools
    handler = functools.partial(Handler, directory=str(EXAMPLES / "parity"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _cfg(**kw):
    return RunConfig(engine=kw.pop("engine", "playwright"), timeout_ms=8000, record_video=False, use_llm=False, headless=kw.pop("headless", True), **kw)


def _run(steps, tmp_path, base=None, data=None, sensitive=None, **cfg):
    t = TestCase(name="Parity", base_url=base, steps=steps, data=data or {}, sensitive=sensitive or [])
    s = run_test(t, _cfg(**cfg), tmp_path / "art")
    return s, tmp_path / "art" / s.run_id


def st(action, target=None, value=None, raw=None):
    return Step(action=Action(action), target=target, value=value, raw=raw or f"{action} {target or ''} {value or ''}".strip())


def errors(s):
    return [(r.index, r.error) for r in s.results if r.error]


# ------------------------------------------------------------------------------------------ one-time passwords
def test_totp_matches_the_rfc_6238_vectors():
    key = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"          # "12345678901234567890"
    for at, want in ((59, "94287082"), (1111111109, "07081804"), (2000000000, "69279037")):
        assert sr.totp(key, digits=8, at=at) == want
    assert len(sr.totp("JBSWY3DPEHPK3PXP")) == 6
    assert sr.totp("jbsw y3dp-ehpk 3pxp", at=100) == sr.totp("JBSWY3DPEHPK3PXP", at=100)      # spaces, dashes and case as authenticator apps show it


def test_totp_in_data_and_the_secret_stays_masked(site, tmp_path):
    seed = "JBSWY3DPEHPK3PXP"
    assert substitute("${totp:seed}", {"seed": seed}) == sr.totp(seed)
    m = Masker({"seed": seed}, ["seed"])
    assert m.is_sensitive_template("${totp:seed}")                                  # the code is masked like the secret it comes from
    s, d = _run([st("navigate", value=site + "/index.html"), st("type", "Name", "${totp:seed}")], tmp_path, data={"seed": seed}, sensitive=["seed"])
    assert s.status.value == "passed", errors(s)
    assert seed not in (d / "summary.json").read_text() and seed not in (d / "test_playwright.py").read_text()
    bad, _ = _run([st("navigate", value=site + "/index.html"), st("type", "Name", "${totp:seed}")], tmp_path, data={"seed": "!!!"})
    assert bad.status.value == "failed" and "totp:seed" in errors(bad)[0][1]


def test_a_code_is_read_from_an_inbox_url(site, tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("H", (http.server.BaseHTTPRequestHandler,), {
        "log_message": lambda *a: None,
        "do_GET": lambda self: (self.send_response(200), self.send_header("Content-Type", "text/html"), self.end_headers(),
                                self.wfile.write(b"<p>Hi! Sent on 2026. Your verification code is <b>482913</b>. Do not share it.</p>"))}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/latest"
        assert sr.code_from(url, timeout=5) == "482913"
        s, _ = _run([st("navigate", value=site + "/index.html"), st("type", "Name", "${code:inbox}")], tmp_path, data={"inbox": url})
        assert s.status.value == "passed", errors(s)
    finally:
        srv.shutdown()
    with pytest.raises(TimeoutError):
        sr.code_from("http://127.0.0.1:9/never", timeout=1)


# ------------------------------------------------------------------------------------------------ English
def test_the_steps_can_be_written_in_english():
    text = """Call GET /api/health expecting status 200 containing "ok"
Mock /api/items to return 500
Block requests to analytics
Verify the page has no accessibility issues
Verify the page looks the same as home within 0.5%
Stop mocking"""
    t, info = parse_text(text, {})
    assert not info["unparsed"], info
    assert [s.action.value for s in t.steps] == ["api", "mock", "mock", "assert_accessible", "assert_visual", "mock"]
    assert json.loads(t.steps[0].value) == {"expect_status": 200, "expect_contains": "ok"} and t.steps[0].target == "GET /api/health"
    assert json.loads(t.steps[1].value)["status"] == 500 and json.loads(t.steps[2].value) == {"block": True}
    assert t.steps[4].value == "home:0.5"


# ------------------------------------------------------------------------------------------------ API calls
@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_an_api_call_is_checked_and_values_are_saved_for_later_steps(engine, site, tmp_path):
    spec = json.dumps({"expect_status": 200, "expect_json": {"status": "ok"}, "save": {"token": "data.token"}})
    s, d = _run([st("navigate", value=site + "/index.html"), st("api", "GET /api/health", spec), st("type", "Name", "${token}"),
                 st("assert_text", None, "Parity page")], tmp_path, engine=engine)
    assert s.status.value == "passed", errors(s)
    assert [r.step.action.value for r in s.results][:2] == ["navigate", "api"]


def test_api_failures_say_what_was_different(site, tmp_path):
    for spec, want in ((json.dumps({"expect_status": 201}), "expected status 201, got 200"),
                       (json.dumps({"expect_contains": "nope"}), "to contain 'nope'"),
                       (json.dumps({"expect_json": {"status": "bad"}}), "expected status = 'bad', got 'ok'")):
        s, _ = _run([st("api", site + "/api/health", spec)], tmp_path)
        assert s.status.value == "failed" and want in errors(s)[0][1], (want, errors(s))
    s, _ = _run([st("api", "GET " + site + "/api/gone")], tmp_path)
    assert "answered 404" in errors(s)[0][1]
    s, _ = _run([st("api", "POST " + site + "/api/orders", json.dumps({"body": {"sku": 7}, "expect_status": 201}))], tmp_path)
    assert s.status.value == "passed" and Handler.posts[-1] == {"sku": 7}


# ------------------------------------------------------------------------------------------------ network mocks
@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_a_mock_changes_what_the_page_gets_and_clearing_it_brings_the_real_answer_back(engine, site, tmp_path):
    s, d = _run([
        st("navigate", value=site + "/index.html"),
        st("mock", "/api/items", json.dumps({"status": 500, "body": '{"error": "boom"}'})),
        st("click", "Load items button"), st("assert_text", None, "Could not load items (500)"),
        st("mock", "/api/items", json.dumps({"status": 200, "body": {"items": ["Mocked fruit"]}})),
        st("click", "Load items button"), st("assert_text", None, "Mocked fruit"),
        st("mock", "/api/items", json.dumps({"block": True})),
        st("click", "Load items button"), st("assert_text", None, "network error"),
        st("mock", None, json.dumps({"clear": True})),
        st("click", "Load items button"), st("assert_text", None, "Loaded 3 items"), st("assert_text", None, "Plums"),
    ], tmp_path, engine=engine)
    assert s.status.value == "passed", errors(s)


# ------------------------------------------------------------------------------------------------ accessibility
@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_the_accessibility_scan_names_the_problems(engine, site, tmp_path):
    s, _ = _run([st("navigate", value=site + "/bad.html"), st("assert_accessible")], tmp_path, engine=engine)
    assert s.status.value == "failed"
    msg = errors(s)[0][1]
    for rule in ("image-alt", "button-name", "link-name", "label", "html-lang"):
        assert rule in msg, msg
    ok, _ = _run([st("navigate", value=site + "/good.html"), st("assert_accessible", None, "minor")], tmp_path, engine=engine)
    assert ok.status.value == "passed", errors(ok)
    only_critical, _ = _run([st("navigate", value=site + "/bad.html"), st("assert_accessible", None, "critical")], tmp_path)
    assert only_critical.status.value == "failed" and "html-lang" not in errors(only_critical)[0][1]       # a serious one: below 'critical'


# ------------------------------------------------------------------------------------------------ visual baselines
def test_visual_baseline_is_made_then_compared_and_a_change_is_caught(site, tmp_path):
    base = tmp_path / "baselines"
    page = site + "/visual.html"
    first, _ = _run([st("navigate", value=page), st("assert_visual", None, "home")], tmp_path, baselines_dir=str(base))
    assert first.status.value == "passed", errors(first)
    assert (base / "parity" / "home.png").exists()                                           # no baseline yet: this one became it
    same, _ = _run([st("navigate", value=page), st("assert_visual", None, "home")], tmp_path, baselines_dir=str(base))
    assert same.status.value == "passed", errors(same)
    changed, d = _run([st("navigate", value=page), st("click", "Change colour button"), st("assert_visual", None, "home")], tmp_path, baselines_dir=str(base))
    assert changed.status.value == "failed" and "does not match its baseline" in errors(changed)[0][1]
    assert (d / "screenshots" / "visual_home_diff.png").stat().st_size > 100               # a picture of what moved
    loose, _ = _run([st("navigate", value=page), st("click", "Change colour button"), st("assert_visual", None, "home:100")], tmp_path, baselines_dir=str(base))
    assert loose.status.value == "passed"                                                    # 100% tolerance accepts anything of the same size
    accepted, _ = _run([st("navigate", value=page), st("click", "Change colour button"), st("assert_visual", None, "home")], tmp_path,
                       baselines_dir=str(base), update_baselines=True)
    assert accepted.status.value == "passed"
    again, _ = _run([st("navigate", value=page), st("click", "Change colour button"), st("assert_visual", None, "home")], tmp_path, baselines_dir=str(base))
    assert again.status.value == "passed", errors(again)                                     # the new look is the baseline now


def test_visual_compare_unit(tmp_path):
    from PIL import Image
    import io

    def png(color, size=(40, 30)):
        b = io.BytesIO()
        Image.new("RGB", size, color).save(b, "PNG")
        return b.getvalue()
    base = tmp_path / "b.png"
    assert sr.visual_compare(png("white"), base)[0] == "created"
    assert sr.visual_compare(png("white"), base)[0] == "same"
    state, msg, ratio, diff = sr.visual_compare(png("black"), base)
    assert state == "different" and ratio == 1.0 and diff
    assert sr.visual_compare(png("white", (41, 30)), base)[0] == "different"                 # a different size is a difference
    assert sr.visual_compare(png((250, 250, 250)), base)[0] == "same"                        # sub-threshold colour noise is not
    assert sr.visual_compare(png("black"), base, update=True)[0] == "created"


# ------------------------------------------------------------------------------------------------ the scripts they leave
def standalone(tmp_path, run_dir, script, env_extra=None):
    alone = tmp_path / f"alone_{script[:-3]}"
    alone.mkdir()
    for f in (script, "autopilot_runtime.py"):
        (alone / f).write_text((run_dir / f).read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / f"res_{script[:-3]}"
    env = {**os.environ, "HEADLESS": "1", "NO_VIDEO": "1", "RESULTS_DIR": str(out), **(env_extra or {})}
    r = subprocess.run([sys.executable, "-I", str(alone / script)], cwd=alone, env=env, capture_output=True, text=True, timeout=300)
    return r, out


def test_scripts_with_these_steps_run_on_their_own(site, tmp_path):
    seed = "JBSWY3DPEHPK3PXP"
    steps = [st("navigate", value=site + "/index.html"),
             st("api", "GET /api/health", json.dumps({"expect_status": 200, "save": {"token": "data.token"}})),
             st("type", "Name", "${totp:seed}"),
             st("mock", "/api/items", json.dumps({"status": 500, "body": ""})),
             st("click", "Load items button"), st("assert_text", None, "Could not load items (500)"),
             st("mock", None, json.dumps({"clear": True})),
             st("click", "Load items button"), st("assert_text", None, "Loaded 3 items"),
             st("assert_visual", None, "items-page:5"),
             st("navigate", value=site + "/good.html"), st("assert_accessible", None, "minor")]
    s, d = _run(steps, tmp_path, data={"seed": seed}, sensitive=["seed"])
    assert s.status.value == "passed", errors(s)
    for script in ("test_playwright.py", "test_selenium.py"):
        src = (d / script).read_text(encoding="utf-8")
        compile(src, script, "exec")
        assert seed not in src
        res, out = standalone(tmp_path, d, script, {"AIAUTO_SECRET_SEED": seed})
        assert res.returncode == 0 and "PASSED" in res.stdout, (script, (res.stdout + res.stderr)[-1500:],
                                                                [x for x in json.loads((out / "summary.json").read_text())["steps"] if x["status"] != "passed"][:2])
