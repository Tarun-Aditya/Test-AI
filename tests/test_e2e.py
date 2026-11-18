"""Full pipeline on the demo page: English -> steps -> locate -> execute -> artifacts -> replay/heal."""
import json
import subprocess
import sys

import pytest

from aiauto.models import RunConfig
from aiauto.nlp.parser import parse_text
from aiauto.runner import load_script, run_test

TEXT = """Open {url}
Type "${{user.name}}" into the username field
Enter ${{user.password}} in password
Check Remember me
Select "${{country}}" from country dropdown
Click on the "Login" button
Verify page contains "Welcome, ${{user.name}} from ${{country}} (remembered)"
Hover over Products menu
Click Laptops
Verify page contains "Laptops page"
"""
DATA = {"user": {"name": "alice", "password": "secret"}, "country": "India"}


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_run_and_replay(engine, demo_page, tmp_path):
    test, info = parse_text(TEXT.format(url=demo_page.as_uri()), DATA)
    assert not info["unparsed"]
    cfg = RunConfig(engine=engine, timeout_ms=5000)
    s = run_test(test, cfg, tmp_path / "art")
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    run_dir = tmp_path / "art" / s.run_id
    for f in ("report.html", "script.json", "test_playwright.py", "test_selenium.py", "summary.json"):
        assert (run_dir / f).exists(), f
    assert s.video and (run_dir / s.video).stat().st_size > 1000
    assert len(list((run_dir / "screenshots").glob("step_*.png"))) == len(test.steps)
    report = (run_dir / "report.html").read_text()
    assert "data:image/png;base64" in report and "<video" in report

    script = load_script(run_dir / "script.json")
    assert script.steps[1].locator and script.steps[1].value == "${user.name}"  # template kept

    # replay with different data, no AI needed
    s2 = run_test(script.model_copy(update={"data": {**DATA, "user": {"name": "bob", "password": "secret"}}}),
                  cfg, tmp_path / "art", mode="replay")
    assert s2.status.value == "passed"
    assert not any(r.healed for r in s2.results)

    # break the stored locators (ids/text-based xpaths change) -> self-healing
    html = demo_page.read_text().replace('id="u"', 'id="user-input"').replace('for="u"', 'for="user-input"') \
        .replace("getElementById('u')", "getElementById('user-input')").replace('name="username"', 'name="login"') \
        .replace('placeholder="Enter username"', 'placeholder="Your user"').replace('id="products-menu"', 'class="pm"')
    demo_page.write_text(html)
    s3 = run_test(script, cfg, tmp_path / "art", mode="replay")
    assert s3.status.value == "passed", [r.error for r in s3.results if r.error]
    # ids / names / placeholders changed: Playwright's role-based locators don't depend on them (no healing
    # needed); Selenium's stored id locators break and self-heal
    if engine == "selenium":
        assert any(r.healed for r in s3.results)
    else:
        assert script.steps[1].locator.strategy == "playwright"


def test_failure_is_reported(demo_page, tmp_path):
    test, _ = parse_text(f'Open {demo_page.as_uri()}\nType "x" in username\nEnter wrong in password\n'
                         'Click Login button\nVerify page contains "Welcome"\nClick Cancel')
    s = run_test(test, RunConfig(timeout_ms=1500, record_video=False), tmp_path / "art")
    assert s.status.value == "failed"
    statuses = [r.status.value for r in s.results]
    assert statuses == ["passed", "passed", "passed", "passed", "failed", "skipped"]
    assert "Welcome" in s.results[4].error
    assert s.results[4].screenshot


def test_generated_scripts_run(demo_page, tmp_path, monkeypatch):
    test, _ = parse_text(TEXT.format(url=demo_page.as_uri()), DATA)
    s = run_test(test, RunConfig(record_video=False), tmp_path / "art")
    run_dir = tmp_path / "art" / s.run_id
    assert (run_dir / "autopilot_runtime.py").exists()          # the scripts' own framework ships with them
    env = {**__import__("os").environ, "HEADLESS": "1", "NO_VIDEO": "1"}
    for script in ("test_playwright.py", "test_selenium.py"):
        out = tmp_path / f"res_{script[:-3]}"
        # copied elsewhere, run without this package on the path: independent of the tool
        alone = tmp_path / f"alone_{script[:-3]}"
        alone.mkdir()
        for f in (script, "autopilot_runtime.py"):
            (alone / f).write_text((run_dir / f).read_text())
        r = subprocess.run([sys.executable, "-I", alone / script], cwd=alone,
                           env={**env, "RESULTS_DIR": str(out), "NO_VIDEO": "0" if "selenium" in script else "1"},
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0 and "PASSED" in r.stdout, r.stderr[-2000:]
        summary = json.loads((out / "summary.json").read_text())
        assert summary["status"] == "passed" and all(st["status"] == "passed" for st in summary["steps"])
        assert all((out / st["shot"]).exists() for st in summary["steps"])            # screenshot per step
        assert "testcase" in (out / "junit.xml").read_text()
        report = (out / "report.html").read_text()
        assert "data:image/png;base64" in report                                      # screenshots embedded
        if "selenium" in script:
            assert summary["video"] and (out / "video.webm").stat().st_size > 1000 and "<video" in report
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"user": {"name": "z", "password": "nope"}}))
    r = subprocess.run([sys.executable, run_dir / "test_selenium.py", bad], cwd=tmp_path, env=env, capture_output=True, timeout=120)
    assert r.returncode != 0
