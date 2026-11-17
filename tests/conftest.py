import functools
import http.server
import shutil
import socket
import threading
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def demo_page(tmp_path):
    """Copy of the demo app in a temp dir (so tests can mutate it)."""
    p = tmp_path / "demo_app.html"
    shutil.copy(EXAMPLES / "demo_app.html", p)
    return p


@pytest.fixture(scope="session")
def shop_url():
    """Serve examples/shop over HTTP (multi-page shop: search -> product -> cart)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(EXAMPLES / "shop"))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setenv("AIAUTO_DISABLE_LLM", "1")
    from aiauto.nlp import llm
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_disabled_reason", None)
