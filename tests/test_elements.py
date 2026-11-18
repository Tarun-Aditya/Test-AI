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


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_canvas_visual_fallback(engine, tmp_path, monkeypatch):
    """Elements drawn on a <canvas> have no DOM: Claude locates them on a screenshot (simulated here: the real
    position of the drawn 'Buy now' button) and the engine clicks there. Without AI the error says why."""
    from aiauto.nlp import llm
    text = f"Open {(EXAMPLES / 'canvas.html').as_uri()}\nClick Buy now\nVerify page contains \"Clicked Buy now\""
    test, _ = parse_text(text, {}, use_llm=False)
    off = run_test(test, RunConfig(engine=engine, timeout_ms=2000, record_video=False, use_llm=True), tmp_path)
    assert off.status.value == "failed" and "canvas" in off.results[1].error

    seen = {}
    def fake_locate(png, step, w, h):
        seen["size"] = (w, h)
        return {"found": True, "x": 50 + 100 + 80, "y": 80 + 100 + 25, "reason": "green 'Buy now' button"}
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "pick_candidate", lambda *a, **k: None)
    monkeypatch.setattr(llm, "locate_visual", fake_locate)
    s = run_test(test, RunConfig(engine=engine, timeout_ms=2000, record_video=False), tmp_path)
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    assert s.results[1].picked_by == "ai-vision" and s.results[1].step.locator.strategy == "point"
    assert seen["size"][0] > 0
    src = (tmp_path / s.run_id / f"test_{engine}.py").read_text()
    assert "located visually" in src and ("page.mouse.click(230, 205)" in src or "move_to_location(230, 205)" in src)


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_cross_origin_iframe_and_closed_shadow_root(engine, xorigin_url, tmp_path):
    text = (f"Open {xorigin_url}/outer.html\nType \"4111111111114242\" into Card number\nClick Pay securely\n"
            "Verify page contains \"Paid with 4242\"\nType \"SAVE10\" into Promo code\nClick Apply promo\n"
            "Verify page contains \"Promo SAVE10 applied\"")
    test, _ = parse_text(text, {}, use_llm=False)
    s = run_test(test, RunConfig(engine=engine, timeout_ms=6000, record_video=False), tmp_path)
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    assert s.results[1].step.locator.frames == ["#pay"]          # cross-origin frame chain recorded
    # without opening closed shadow roots the promo field is unreachable
    closed = run_test(test, RunConfig(engine=engine, timeout_ms=1500, record_video=False, open_closed_shadow_roots=False), tmp_path)
    assert closed.status.value == "failed" and "Promo code" in (closed.results[4].error or "")
    import os, subprocess, sys
    env = {**os.environ, "HEADLESS": "1", "NO_VIDEO": "1"}
    for script in ("test_playwright.py", "test_selenium.py"):
        r = subprocess.run([sys.executable, str(tmp_path / s.run_id / script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0 and "PASSED" in r.stdout, (script, r.stderr[-1500:])


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_virtualised_list_and_lazy_section(engine, tmp_path):
    from aiauto.runner import load_script
    text = (f"Open {(EXAMPLES / 'virtual.html').as_uri()}\nClick Open order 750\nVerify page contains \"Opened order 750\"\n"
            "Click Subscribe to newsletter\nVerify page contains \"Subscribed\"")
    test, _ = parse_text(text, {}, use_llm=False)
    s = run_test(test, RunConfig(engine=engine, timeout_ms=8000, record_video=False), tmp_path)
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    for other in ("playwright", "selenium"):      # replays re-find row 750 and never click a different row
        s2 = run_test(load_script(tmp_path / s.run_id / "script.json"), RunConfig(engine=other, timeout_ms=8000, record_video=False),
                      tmp_path, mode="replay")
        assert s2.status.value == "passed", (other, [r.error for r in s2.results if r.error])
