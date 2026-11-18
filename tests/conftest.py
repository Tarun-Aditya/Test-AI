import functools
import http.server
import shutil
import socket
import threading
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def pytest_configure(config):
    config.addinivalue_line("markers", "live_ai: calls a real AI provider (your subscription / key); run with --live-ai")


def pytest_addoption(parser):
    parser.addoption("--live-ai", action="store_true", help="run tests that call a real AI provider")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live-ai"):
        return
    skip = pytest.mark.skip(reason="needs --live-ai (calls a real AI provider)")
    for item in items:
        if "live_ai" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_ai(request, monkeypatch):
    """Tests never reach a real AI provider (a logged-in CLI or a key on the machine) unless marked live_ai;
    tests of the AI path inject a fake provider instead."""
    from aiauto.nlp import providers
    providers.configure(None)
    monkeypatch.setenv("AIAUTO_AUTH", "off")         # single-user mode unless a test turns accounts on
    if "live_ai" not in request.keywords:
        monkeypatch.setenv("AIAUTO_DISABLE_LLM", "1")
    yield
    providers.configure(None)


@pytest.fixture
def demo_page(tmp_path):
    """Copy of the demo app in a temp dir (so tests can mutate it)."""
    p = tmp_path / "demo_app.html"
    shutil.copy(EXAMPLES / "demo_app.html", p)
    return p


def _serve(directory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass
    handler = functools.partial(Quiet, directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="session")
def shop_url():
    """Serve examples/shop over HTTP (multi-page shop: search -> product -> cart)."""
    srv, url = _serve(EXAMPLES / "shop")
    yield url
    srv.shutdown()


@pytest.fixture(scope="session")
def xorigin_url():
    """examples/xorigin: page on 127.0.0.1 embedding an iframe from localhost (a different origin)."""
    srv, url = _serve(EXAMPLES / "xorigin")
    yield url
    srv.shutdown()

