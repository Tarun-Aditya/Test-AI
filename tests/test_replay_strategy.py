"""The replay strategy, layer by layer: stored locator -> fallback locators (with fresh ones written back) -> heal by description ->
fail clearly (never guess, never heal a check). The same Runner replays in the app (Playwright or Selenium) and in the extension's tab."""
import json
import subprocess
import sys
import threading
import http.server

import pytest

from aiauto.models import RunConfig
from aiauto.nlp.parser import parse_text
from aiauto.runner import load_script, run_test

V1 = """<!doctype html><title>Contact</title><body style="font:16px system-ui;margin:30px">
<label for="em">Email address</label> <input id="em" type="email" placeholder="you@example.com">
<button id="send" onclick="document.getElementById('out').textContent='Sent to ' + document.getElementById('em').value">Send message</button>
<div id="out"></div></body>"""
# every stored locator breaks (label text, id, placeholder, name), but the field is still plainly "the email"
V2 = V1.replace('<label for="em">Email address</label>', '<label for="contact-mail-9f3a">Contact email</label>').replace('id="em"', 'id="contact-mail-9f3a"') \
       .replace("getElementById('em')", "getElementById('contact-mail-9f3a')").replace('placeholder="you@example.com"', 'placeholder="name@company.com"')
# two equally good matches
V3 = V1.replace("</button>", "</button><br><label for='em2'>Email address</label> <input id='em2' type='email'>")
# the page now says something else
V4 = V1.replace("Sent to ", "Message failed for ")


class Site(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        me = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = me.html.encode()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.html = V1
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/"

    def run(self):
        self.srv.serve_forever()


@pytest.fixture
def site():
    s = Site()
    s.start()
    yield s
    s.srv.shutdown()


CFG = dict(record_video=False, use_llm=False, timeout_ms=4000)
SCENARIO = 'Open {url}\nType "me@example.com" into Email address\nClick Send message\nVerify page contains "Sent to me@example.com"'


def first_run(site, tmp_path, engine="playwright"):
    test, _ = parse_text(SCENARIO.format(url=site.url), {}, use_llm=False)
    s = run_test(test, RunConfig(engine=engine, **CFG), tmp_path / "art")
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    return tmp_path / "art" / s.run_id


def replay(site, run_dir, tmp_path, engine="playwright", html=None):
    if html is not None:
        site.html = html
    return run_test(load_script(run_dir / "script.json"), RunConfig(engine=engine, **CFG), tmp_path / "art2", mode="replay")


def test_a_page_that_did_not_change_replays_with_the_stored_locators(site, tmp_path):
    run = first_run(site, tmp_path)
    s = replay(site, run, tmp_path)
    assert s.status.value == "passed" and s.healed_steps == 0
    assert all(r.picked_by in ("script", None) for r in s.results)


def test_a_fallback_locator_is_used_and_fresh_ones_are_written_back(site, tmp_path):
    run = first_run(site, tmp_path)
    site.html = V1.replace('id="em"', 'id="email-field"').replace('for="em"', 'for="email-field"').replace("getElementById('em')", "getElementById('email-field')")   # the id changed only
    s = replay(site, run, tmp_path)
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    script = json.loads((tmp_path / "art2" / s.run_id / "script.json").read_text(encoding="utf-8"))
    loc = script["steps"][1]["locator"]
    assert "em\"" not in json.dumps(loc) and "#em" not in json.dumps(loc)       # the stale id is gone from what the script now carries


def test_when_every_stored_locator_breaks_the_element_is_found_by_its_description_and_the_script_is_updated(site, tmp_path):
    run = first_run(site, tmp_path)
    s = replay(site, run, tmp_path, html=V2)
    detail = [(r.step.raw, r.status.value, r.error, r.picked_by, r.healed) for r in s.results]
    assert s.status.value == "passed", detail
    assert s.healed_steps >= 1, detail                                           # the step the page broke was healed, and counted
    new_dir = tmp_path / "art2" / s.run_id
    old = json.loads((run / "script.json").read_text(encoding="utf-8"))["steps"][1]["locator"]
    new = json.loads((new_dir / "script.json").read_text(encoding="utf-8"))["steps"][1]["locator"]
    assert new != old
    # the healed script works as it is on the changed page, on both engines' scripts
    for script in ("test_playwright.py", "test_selenium.py"):
        alone = tmp_path / f"alone_{script[:-3]}"
        alone.mkdir()
        for f in (script, "autopilot_runtime.py"):
            (alone / f).write_text((new_dir / f).read_text(encoding="utf-8"), encoding="utf-8")
        import os
        r = subprocess.run([sys.executable, "-I", str(alone / script)], cwd=alone, env={**os.environ, "HEADLESS": "1", "NO_VIDEO": "1", "RESULTS_DIR": str(tmp_path / f"res_{script[:-3]}")},
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0 and "PASSED" in r.stdout, (script, (r.stdout + r.stderr)[-1500:])


def test_two_equally_good_matches_fail_clearly_instead_of_guessing(site, tmp_path):
    """When the engine has to pick an element from a description and two are equally good, it says so instead of guessing."""
    site.html = ('<!doctype html><title>Contact</title><body><section><label for="a1">Email address</label> <input id="a1" type="email"></section>'
                 '<section><label for="b1">Email address</label> <input id="b1" type="email"></section><button id="send">Send message</button></body>')
    test, _ = parse_text(f'Open {site.url}\nType "me@example.com" into Email address', {}, use_llm=False)
    s = run_test(test, RunConfig(**CFG), tmp_path / "art")
    bad = [r for r in s.results if r.status.value == "failed"]
    assert s.status.value == "failed" and bad
    assert "equally well" in bad[0].error and "Say which one" in bad[0].error, bad[0].error


def test_a_check_is_never_healed_when_the_page_says_something_else(site, tmp_path):
    run = first_run(site, tmp_path)
    s = replay(site, run, tmp_path, html=V4)
    bad = [r for r in s.results if r.status.value == "failed"]
    assert s.status.value == "failed" and bad and bad[-1].step.action.value == "assert_text"
    assert "Sent to me@example.com" in bad[-1].error


def test_missing_data_fails_with_a_clear_message(site, tmp_path):
    test, _ = parse_text(f'Open {site.url}\nType ${{secret}} into Email address', {"secret": "x"}, use_llm=False)
    s = run_test(test.model_copy(update={"data": {}}), RunConfig(**CFG), tmp_path / "art")
    bad = [r for r in s.results if r.status.value == "failed"]
    assert s.status.value == "failed" and bad and "secret" in bad[0].error


@pytest.mark.parametrize("engine", ["selenium"])
def test_the_same_script_replays_on_the_other_engine_with_the_same_strategy(site, tmp_path, engine):
    run = first_run(site, tmp_path)                    # made with Playwright
    s = replay(site, run, tmp_path, engine=engine)     # replayed with Selenium: locators it can't run are skipped, not "healed"
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    s2 = replay(site, run, tmp_path, engine=engine, html=V2)
    assert s2.status.value == "passed" and s2.healed_steps >= 1, [r.error for r in s2.results if r.error]
