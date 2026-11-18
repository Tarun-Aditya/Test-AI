"""Every engine x browser this machine has: the shop flow through the engine, then its generated script.

In the Docker image all nine combinations exist (Playwright: Chromium, Chrome, Edge, Firefox, WebKit;
Selenium: Chromium, Chrome, Edge, Firefox). On other machines the missing browsers are skipped.
  docker run --rm --shm-size=2g -v "$PWD:/src" -w /src autopilot sh -c "pip install -q pytest && pytest -q tests/test_browsers.py"
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aiauto.models import RunConfig
from aiauto.nlp.parser import parse_text
from aiauto.runner import run_test

from .conftest import EXAMPLES

TEXT = (EXAMPLES / "shop_natural.txt").read_text()
CHROME_PATHS = ["/opt/google/chrome/chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
EDGE_PATHS = ["/opt/microsoft/msedge/msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def _pw_has(browser):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if browser in ("chrome", "msedge"):
            exe = {"chrome": CHROME_PATHS + ["google-chrome"], "msedge": EDGE_PATHS + ["microsoft-edge"]}[browser]
            return any(os.path.exists(e) or shutil.which(e) for e in exe)
        bt = {"chromium": p.chromium, "firefox": p.firefox, "webkit": p.webkit}[browser]
        try:
            return os.path.exists(bt.executable_path)
        except Exception:
            return False


def _se_has(browser):
    if browser == "chromium":
        return bool(os.getenv("AIAUTO_CHROME_BINARY"))
    if browser == "chrome":
        return bool(os.getenv("AIAUTO_GOOGLE_CHROME") or shutil.which("google-chrome") or any(os.path.exists(p) for p in CHROME_PATHS))
    if browser == "msedge":
        return bool(shutil.which("microsoft-edge") or any(os.path.exists(p) for p in EDGE_PATHS))
    if browser == "firefox":
        return bool(os.getenv("AIAUTO_FIREFOX_BINARY") or shutil.which("firefox") or
                    os.path.exists("/Applications/Firefox.app") or os.path.exists(r"C:\Program Files\Mozilla Firefox\firefox.exe"))
    if browser == "safari":
        return sys.platform == "darwin" and os.path.exists("/usr/bin/safaridriver")
    return False


MATRIX = [("playwright", b) for b in ("chromium", "chrome", "msedge", "firefox", "webkit")] + \
         [("selenium", b) for b in ("chromium", "chrome", "msedge", "firefox", "safari")]


@pytest.mark.parametrize("engine,browser", MATRIX, ids=[f"{e}-{b}" for e, b in MATRIX])
def test_shop_on_every_browser(engine, browser, shop_url, tmp_path):
    if not (_pw_has if engine == "playwright" else _se_has)(browser):
        pytest.skip(f"{browser} is not installed for {engine} on this machine")
    data = {"base_url": shop_url, "product": "wireless mouse", "brand": "Logitech", "qty": "2"}
    test, _ = parse_text(TEXT, data, use_llm=False, sensitive=["qty"])
    s = run_test(test, RunConfig(engine=engine, browser=browser, headless=browser != "safari", record_video=True, use_llm=False,
                                 timeout_ms=15000), tmp_path / "a")
    assert s.status.value == "passed", [(r.index, r.error) for r in s.results if r.error]
    run_dir = tmp_path / "a" / s.run_id
    assert s.video and (run_dir / s.video).stat().st_size > 1000, "video"
    assert all(r.screenshot for r in s.results), [(r.index, r.step.action.value, r.status.value, r.error) for r in s.results if not r.screenshot]
    # the generated script on the same browser, standalone
    out = tmp_path / "script_out"
    r = subprocess.run([sys.executable, str(run_dir / f"test_{engine}.py")], cwd=run_dir, capture_output=True, text=True,
                       timeout=240, env={**os.environ, "BROWSER": browser, "HEADLESS": "0" if browser == "safari" else "1", "RESULTS_DIR": str(out),
                                         "AIAUTO_SECRET_QTY": "2"})
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    summary = json.loads((out / "summary.json").read_text())
    assert summary["browser"] == browser and summary["status"] == "passed" and summary["video"]


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_headed_on_a_virtual_display(engine, shop_url, tmp_path):
    """Headed (a real, visible browser window) on the container's Xvfb screen."""
    if sys.platform.startswith("linux") and not os.getenv("DISPLAY"):
        pytest.skip("no display (run inside the Docker image, or under xvfb-run)")
    browser = "chromium" if _pw_has("chromium") and (engine == "playwright" or _se_has("chromium")) else "chrome"
    test, _ = parse_text(TEXT, {"base_url": shop_url, "product": "wireless mouse", "brand": "Logitech", "qty": "1"}, use_llm=False)
    s = run_test(test, RunConfig(engine=engine, browser=browser, headless=False, record_video=True, use_llm=False), tmp_path)
    assert s.status.value == "passed", [(r.index, r.error) for r in s.results if r.error]
    assert (Path(tmp_path) / s.run_id / s.video).stat().st_size > 1000
