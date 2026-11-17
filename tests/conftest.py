import os
import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def demo_page(tmp_path):
    """Copy of the demo app in a temp dir (so tests can mutate it)."""
    p = tmp_path / "demo_app.html"
    shutil.copy(EXAMPLES / "demo_app.html", p)
    return p


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setenv("AIAUTO_DISABLE_LLM", "1")
    from aiauto.nlp import llm
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_disabled_reason", None)


def have_browser() -> bool:
    try:
        import playwright  # noqa
        return True
    except ImportError:
        return False
