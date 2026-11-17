"""Search -> first matching product -> add to cart -> cart, on a local multi-page shop."""
import io
import json
import os
import subprocess
import sys
import zipfile

import pytest

from aiauto.models import RunConfig
from aiauto.nlp.parser import parse_text
from aiauto.runner import Runner, run_test

from .conftest import EXAMPLES


def _shop_test(shop_url, **data):
    d = {"base_url": shop_url, "product": "wireless mouse", "brand": "Logitech", "qty": "2", **data}
    test, info = parse_text((EXAMPLES / "shop_cart.txt").read_text(), d)
    assert not info["unparsed"]
    return test


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_add_to_cart_and_scripts(engine, shop_url, tmp_path):
    s = run_test(_shop_test(shop_url), RunConfig(engine=engine, record_video=False), tmp_path)
    assert s.status.value == "passed", [r.error for r in s.results if r.error]
    assert [r.status.value for r in s.results].count("skipped") == 1      # optional cookie banner absent
    run_dir = tmp_path / s.run_id
    env = {**os.environ, "HEADLESS": "1", "NO_VIDEO": "1"}
    hp = tmp_path / "hp.json"
    hp.write_text(json.dumps({"base_url": shop_url, "brand": "HP", "qty": "1"}))
    for script in ("test_playwright.py", "test_selenium.py"):
        for args in ([], [str(hp)]):          # positional picks are data-driven in the generated code
            r = subprocess.run([sys.executable, str(run_dir / script), *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
            assert r.returncode == 0 and "PASSED" in r.stdout, (script, args, r.stderr[-1500:])


def test_session_saved_by_playwright_loads_in_selenium(shop_url, tmp_path):
    holder = []
    cfg = RunConfig(engine="playwright", record_video=False, save_session="shop")
    s = run_test(_shop_test(shop_url), cfg, tmp_path, runner_out=holder)
    assert s.status.value == "passed" and holder[0].saved_session
    test, _ = parse_text(f'Open {shop_url}/cart.html then verify page contains "Subtotal (2 items)"')
    with_session = RunConfig(engine="selenium", record_video=False, timeout_ms=3000)
    with_session.session_state = holder[0].saved_session
    assert run_test(test, with_session, tmp_path).status.value == "passed"
    assert run_test(test, RunConfig(engine="selenium", record_video=False, timeout_ms=3000), tmp_path).status.value == "failed"
