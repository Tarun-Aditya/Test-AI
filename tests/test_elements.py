"""Every kind of element on both engines: nested shadow roots, nested iframes, dynamic ids, dialogs,
drag & drop, custom dropdowns, date/range/color, contenteditable, multi-select, upload, hover menus."""
import json
import subprocess
import sys

import pytest

from aiauto.data import load_data
from aiauto.models import RunConfig
from aiauto.nlp.parser import parse_text
from aiauto.runner import run_test
from aiauto.scriptgen import extract_spec

from .conftest import EXAMPLES


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_playground(engine, tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("cv")
    text = (EXAMPLES / "playground_test.txt").read_text().replace("PLAYGROUND_URL", (EXAMPLES / "playground.html").as_uri()) \
        .replace("UPLOAD_FILE", str(resume))
    test, info = parse_text(text, load_data(str(EXAMPLES / "playground_data.json")))
    assert not info["unparsed"]
    s = run_test(test, RunConfig(engine=engine, timeout_ms=5000), tmp_path / "art")
    assert s.status.value == "passed", [(r.index + 1, r.error) for r in s.results if r.error]
    run_dir = tmp_path / "art" / s.run_id
    labels = [r.step.locator.label() for r in s.results if r.step.locator]
    assert any("shadow[user-card] » shadow[address-form]" in l for l in labels)         # nested shadow roots
    assert any(l.count("iframe[") == 2 for l in labels)                                   # nested iframes
    assert any("role=" in l for l in labels)                                              # semantic native locators
    # secrets never land in artifacts
    for f in run_dir.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".py", ".html"):
            body = f.read_text(errors="ignore")
            assert "S3cret!!" not in body and "4111111111114242" not in body, f
    assert s.video and (run_dir / s.video).stat().st_size > 10_000
    # generated scripts are valid Python carrying the spec, and contain no healing logic
    for name in ("test_playwright.py", "test_selenium.py"):
        src = (run_dir / name).read_text()
        compile(src, name, "exec")
        assert len(extract_spec(src).steps) == len(test.steps)
        assert "snapshot" not in src and "heal" not in src.lower()


def test_cross_engine_replay_self_heals(tmp_path):
    """A script recorded with Playwright replays on Selenium; locators Selenium can't evaluate in a shadow root self-heal."""
    resume = tmp_path / "resume.txt"
    resume.write_text("x")
    text = (EXAMPLES / "playground_test.txt").read_text().replace("PLAYGROUND_URL", (EXAMPLES / "playground.html").as_uri()) \
        .replace("UPLOAD_FILE", str(resume))
    data = load_data(str(EXAMPLES / "playground_data.json"))
    test, _ = parse_text(text, data)
    s1 = run_test(test, RunConfig(engine="playwright", timeout_ms=5000, record_video=False), tmp_path / "a")
    assert s1.status.value == "passed", [(r.index + 1, r.error) for r in s1.results if r.error]
    script = extract_spec((tmp_path / "a" / s1.run_id / "test_playwright.py").read_text())
    from aiauto.data import normalize
    plain, sens = normalize(data)
    s2 = run_test(script.model_copy(update={"data": plain, "sensitive": sens}),
                  RunConfig(engine="selenium", timeout_ms=5000, record_video=False), tmp_path / "a", mode="replay")
    assert s2.status.value == "passed", [(r.index + 1, r.error) for r in s2.results if r.error]
    assert s2.healed_steps > 0


def test_selenium_uses_chrome_accessibility_tree(tmp_path):
    """On Chrome, the top-ranked Selenium candidates take role + accessible name from the CDP accessibility tree."""
    import time
    from aiauto.engines.selenium_engine import SeleniumEngine
    e = SeleniumEngine(RunConfig(engine="selenium", record_video=False), tmp_path)
    e.start()
    try:
        e.goto((EXAMPLES / "playground.html").as_uri())
        time.sleep(1.7)
        cands = e.snapshot()
        combo = next(c for c in cands if c.get("role") == "combobox" and c["tag"] == "div")
        shadow = next(c for c in cands if c["shadow"] and c["tag"] == "input")
        framed = next(c for c in cands if c["frames"] and c["tag"] == "button")
        assert e.refine([combo, shadow, framed]) == 3
        assert combo["name"] == "Size" and combo["js_name"] == "Pick a size"     # aria-labelledby, as Chrome computes it
        assert all(c.get("ax") for c in (combo, shadow, framed))
        d = e.describe(combo)
        assert {"strategy": "role", "value": "Size", "role": "combobox"} in d["locators"]
    finally:
        e.stop()


def test_ambiguous_target_fails_instead_of_guessing(tmp_path):
    test, _ = parse_text(f"Open {(EXAMPLES / 'playground.html').as_uri()}\nClick View", {})
    s = run_test(test, RunConfig(timeout_ms=3000, record_video=False), tmp_path)
    assert s.status.value == "failed" and "Ambiguous" in s.results[1].error and "match equally well" in s.results[1].error
