"""The Claude path, exercised against a mock HTTP transport (no network, no key)."""
import json

import anthropic
import httpx2 as httpx

from aiauto.models import Action
from aiauto.nlp import llm, parser


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
    monkeypatch.setattr(llm, "_client", _mock_client(payload, seen))
    test, info = parser.parse_text("open x.io and login with my email", {"email": "a@b.c"})
    assert info["parser"].startswith("llm:")
    assert [s.action for s in test.steps] == [Action.NAVIGATE, Action.TYPE, Action.CLICK]
    body = seen[0]["body"]
    assert body["model"] == llm.MODEL
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0]["headers"]["anthropic-beta"]
    assert "email" in body["messages"][0]["content"]


def test_llm_refusal_falls_back_to_rules(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "x",
                                         "content": [], "stop_reason": "refusal", "stop_sequence": None,
                                         "usage": {"input_tokens": 1, "output_tokens": 0}})
    monkeypatch.setattr(llm, "_client", anthropic.Anthropic(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    test, info = parser.parse_text("Click Login")
    assert info["parser"] == "rules" and test.steps[0].action == Action.CLICK
