"""The Claude path, exercised against a mock HTTP transport (no network, no key)."""
import json

import anthropic
import httpx2 as httpx

import pytest

from aiauto.models import Action
from aiauto.nlp import llm, parser, providers


@pytest.fixture(autouse=True)
def _anthropic_only(monkeypatch):
    """These tests drive the Anthropic-key provider with a mock HTTP transport."""
    monkeypatch.delenv("AIAUTO_DISABLE_LLM", raising=False)
    providers.configure({"provider": "anthropic"})


def _mock_client(payload, seen):
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append({"body": body, "headers": dict(request.headers)})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })
    return anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_llm_parse(monkeypatch):
    seen = []
    payload = {"name": "Login", "steps": [
        {"action": "navigate", "target": None, "value": "https://x.io", "raw": "open x.io"},
        {"action": "type", "target": "Email field", "value": "${email}", "raw": "login with email"},
        {"action": "click", "target": "Sign in button", "value": None, "raw": "login"},
    ]}
    monkeypatch.setattr(providers, "_anthropic_client", _mock_client(payload, seen))
    test, info = parser.parse_text("open x.io and login with my email", {"email": "a@b.c"})
    assert info["parser"].startswith("llm:")
    assert [s.action for s in test.steps] == [Action.NAVIGATE, Action.TYPE, Action.CLICK]
    body = seen[0]["body"]
    assert body["model"] == providers.DEFAULT_MODEL
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0]["headers"]["anthropic-beta"]
    assert "email" in body["messages"][0]["content"]


def test_llm_refusal_falls_back_to_rules(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "x",
                                         "content": [], "stop_reason": "refusal", "stop_sequence": None,
                                         "usage": {"input_tokens": 1, "output_tokens": 0}})
    monkeypatch.setattr(providers, "_anthropic_client", anthropic.Anthropic(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    test, info = parser.parse_text("Click Login")
    assert info["parser"] == "rules" and test.steps[0].action == Action.CLICK


def _fake_cli(tmp_path, name, stdout, exit_code=0):
    """A stand-in for a provider CLI: records its argv/stdin and prints a canned answer.
    Returned as a command list (python + script) so it runs the same way on Windows, macOS and Linux."""
    import sys
    p = tmp_path / f"{name}.py"
    log = tmp_path / f"{name}.args"
    p.write_text("import json, sys\n"
                 f"open({str(log)!r}, 'w', encoding='utf-8').write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}))\n"
                 f"sys.stdout.write({stdout!r})\nsys.exit({exit_code})\n", encoding="utf-8")
    return [sys.executable, str(p)], log


STEPS = {"name": "Login", "steps": [{"action": "click", "target": "Sign in button", "value": None, "nth": None,
                                     "optional": False, "raw": "sign in"}]}


def test_claude_subscription_cli(tmp_path):
    out = json.dumps({"type": "result", "is_error": False, "result": json.dumps(STEPS), "structured_output": STEPS})
    exe, log = _fake_cli(tmp_path, "claude", out)
    providers.configure({"provider": "claude-code", "aiauto_claude_cli": exe, "model": "claude-sonnet-5-5"})
    test, info = parser.parse_text("sign in")
    assert info["parser"] == "llm:claude-code:claude-sonnet-5-5" and test.steps[0].target == "Sign in button"
    call = json.loads(log.read_text(encoding='utf-8'))
    a = call["argv"]
    assert a[0] == "-p" and a[a.index("--output-format") + 1] == "json" and "--json-schema" in a
    assert a[a.index("--model") + 1] == "claude-sonnet-5-5" and a[a.index("--tools") + 1] == ""   # no tools: answer only
    assert "sign in" in call["stdin"]


def test_cursor_and_copilot_clis_parse_json_from_text(tmp_path):
    exe, log = _fake_cli(tmp_path, "cursor-agent", json.dumps({"type": "result", "result": "Here you go:\n```json\n" + json.dumps(STEPS) + "\n```"}))
    providers.configure({"provider": "cursor", "aiauto_cursor_cli": exe, "model": "gpt-5"})
    assert parser.parse_text("sign in")[1]["parser"] == "llm:cursor:gpt-5"
    assert "JSON Schema" in json.loads(log.read_text(encoding='utf-8'))["argv"][-1]          # schema travels in the prompt
    exe, _ = _fake_cli(tmp_path, "copilot", "Sure. " + json.dumps(STEPS))
    providers.configure({"provider": "copilot", "aiauto_copilot_cli": exe})
    test, info = parser.parse_text("sign in")
    assert info["parser"] == "llm:copilot:default" and test.steps[0].action == Action.CLICK


def test_auto_skips_a_subscription_that_is_not_logged_in(tmp_path, monkeypatch):
    """auto: a CLI that says "not logged in" is dropped and the next provider (here an API key) answers."""
    bad, _ = _fake_cli(tmp_path, "claude", json.dumps({"type": "result", "is_error": True, "api_error_status": 401,
                                                       "result": "Not logged in. Please run /login"}))
    seen = []
    providers.configure({"aiauto_claude_cli": bad, "anthropic_key": "sk-test"})
    for k in ("cursor", "copilot"):
        providers._failed[k] = "not installed"                      # pretend these are absent on this machine
    monkeypatch.setattr(providers, "_anthropic_client", _mock_client(STEPS, seen))
    test, info = parser.parse_text("sign in")
    assert info["parser"].startswith("llm:anthropic") and seen
    assert "claude-code" in providers._failed and not providers.detect("claude-code")[0]


def test_litellm_any_model(monkeypatch):
    pytest.importorskip("litellm")
    monkeypatch.setenv("AIAUTO_LITELLM_MOCK", json.dumps(STEPS))
    providers.configure({"provider": "litellm", "litellm_model": "openai/gpt-4o-mini", "litellm_key": "sk-test"})
    test, info = parser.parse_text("sign in")
    assert info["parser"] == "llm:litellm:openai/gpt-4o-mini" and test.steps[0].target == "Sign in button"


@pytest.mark.live_ai
def test_live_subscription_parses_natural_english():
    """Real call through whatever provider this machine has (run: pytest --live-ai)."""
    from .conftest import EXAMPLES
    providers.configure(None)                       # auto: subscription first
    assert providers.available(), providers.status()
    test, info = parser.parse_text((EXAMPLES / "shop_natural.txt").read_text(),
                                   {"base_url": "http://x", "product": "mouse", "brand": "Logitech", "qty": "2"})
    assert info["parser"].startswith("llm:")
    acts = [s.action.value for s in test.steps]
    assert acts[0] == "navigate" and "press" in acts and acts[-1] == "assert_text" and len(acts) >= 6
