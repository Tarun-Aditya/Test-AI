"""Claude-backed intent/value extraction and locator disambiguation.

Enabled when an Anthropic credential is available (ANTHROPIC_API_KEY, or a profile from
`ant auth login`). Everything here degrades gracefully: callers fall back to the rule parser
and heuristic locator scoring when the model is unavailable or declines.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..models import Action, Step

log = logging.getLogger(__name__)

MODEL = os.getenv("AIAUTO_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("AIAUTO_EFFORT", "low")   # parsing English into steps is a light task

_ACTIONS = [a.value for a in Action]

STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Short test name"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": _ACTIONS},
                    "target": {"type": ["string", "null"]},
                    "value": {"type": ["string", "null"]},
                    "raw": {"type": "string"},
                },
                "required": ["action", "target", "value", "raw"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "steps"],
    "additionalProperties": False,
}

PARSE_SYSTEM = """You convert plain-English browser test instructions into a precise list of browser steps.

For each step output:
- action: one of the allowed enum values.
- target: a short natural-language description of the element exactly as a human would identify it on screen
  (visible text, label, placeholder or role), e.g. "Login button", "Email field", "Products menu". null when
  the action does not touch an element (navigate, wait <seconds>, press without a field, assert_url, assert_title,
  back, refresh, scroll up/down, screenshot).
- value: the data for the action: URL for navigate (always with scheme), text for type/append, key name for press
  (Playwright key names: Enter, Tab, Escape, ArrowDown, Control+A), option label for select, expected text for
  assert_text/assert_url/assert_title, seconds for wait, "down"/"up"/"top"/"bottom" (optionally ":<pixels>") for
  scroll, file path for upload. Otherwise null.
- raw: the fragment of the original instruction this step came from.

Rules:
- Keep data placeholders like ${user.email} or {{password}} verbatim in value; never invent their contents.
- One sentence may contain several steps ("type x in y and press enter" is two steps).
- "Login with alice / secret" style shorthand expands into the individual type and click steps.
- Use assert_text with target null when the text may be anywhere on the page.
- Do not add steps the user did not ask for."""

PICK_SYSTEM = """You pick which DOM element a browser test step refers to.
You get a step (action + natural-language target) and a numbered list of candidate elements with their
attributes. Answer with the index of the single best candidate, or -1 if none plausibly matches."""

PICK_SCHEMA = {
    "type": "object",
    "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["index", "reason"],
    "additionalProperties": False,
}

_client = None
_disabled_reason: str | None = None


def _get_client():
    global _client, _disabled_reason
    if _client is not None or _disabled_reason:
        return _client
    if os.getenv("AIAUTO_DISABLE_LLM"):
        _disabled_reason = "disabled by AIAUTO_DISABLE_LLM"
        return None
    try:
        import anthropic
    except ImportError:
        _disabled_reason = "anthropic package not installed"
        return None
    has_cred = any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) or \
        os.path.isdir(os.path.expanduser("~/.config/anthropic"))
    if not has_cred:
        _disabled_reason = "no Anthropic credentials found"
        return None
    _client = anthropic.Anthropic()
    return _client


def available() -> bool:
    return _get_client() is not None


def status() -> str:
    return f"enabled ({MODEL})" if available() else f"disabled: {_disabled_reason}"


def _structured_call(system: str, user: str, schema: dict, max_tokens: int = 16000) -> dict | None:
    client = _get_client()
    if client is None:
        return None
    import anthropic
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            # If the primary model declines, the API re-runs the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        global _disabled_reason, _client
        _disabled_reason, _client = f"authentication failed: {e}", None
        log.warning("LLM disabled: %s", e)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("LLM unreachable, falling back to rules: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("LLM error %s, falling back to rules: %s", e.status_code, e)
        return None
    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("LLM stop_reason=%s, falling back to rules", resp.stop_reason)
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_steps(text: str, data_keys: list[str] | None = None) -> tuple[str, list[Step]] | None:
    user = f"Instructions:\n{text}"
    if data_keys:
        user += "\n\nAvailable data variables (reference them as ${name}): " + ", ".join(data_keys)
    out = _structured_call(PARSE_SYSTEM, user, STEP_SCHEMA)
    if not out:
        return None
    steps = []
    for s in out.get("steps", []):
        try:
            steps.append(Step(action=Action(s["action"]), target=s.get("target"), value=s.get("value"), raw=s.get("raw", "")))
        except (ValueError, KeyError):
            continue
    return out.get("name") or "Untitled test", steps


def pick_candidate(step: Step, candidates: list[dict]) -> int | None:
    if not candidates:
        return None
    lines = []
    for i, c in enumerate(candidates):
        attrs = {k: v for k, v in c.items() if v and k not in ("score", "css", "xpath", "rect")}
        lines.append(f"[{i}] {json.dumps(attrs, ensure_ascii=False)[:400]}")
    user = (f"Step: action={step.action.value} target={step.target!r} value={step.value!r}\n"
            f"Original sentence: {step.raw!r}\n\nCandidates:\n" + "\n".join(lines))
    out = _structured_call(PICK_SYSTEM, user, PICK_SCHEMA, max_tokens=4000)
    if not out:
        return None
    idx = out.get("index")
    return idx if isinstance(idx, int) and 0 <= idx < len(candidates) else None
