"""Where the AI calls go: your own subscription by default, an API key, or any model through LiteLLM.

    claude-code   Claude subscription through the Claude Code CLI you are logged into (`claude -p`)
    cursor        Cursor subscription through the Cursor CLI (`cursor-agent -p`)
    copilot       GitHub Copilot subscription through the Copilot CLI (`copilot -p`)
    anthropic     Anthropic API key (ANTHROPIC_API_KEY / `ant auth login`), Anthropic SDK with structured outputs
    litellm       any model LiteLLM supports (OpenAI, Gemini, Azure, Bedrock, Mistral, Ollama, a LiteLLM proxy, ...)

`auto` (the default) tries them in that order: subscriptions first, then a key, then LiteLLM. A provider that
fails because it is not logged in / has no credits / is not installed is skipped for the rest of the process
and the next one is used; if none works, callers fall back to the rule parser and heuristic locator scoring.

Every provider answers the same question - "return JSON matching this schema" - so the rest of the tool does not
care which one is used. Settings come from the hub's AI settings (web: Settings -> AI) or environment variables:
AIAUTO_AI_PROVIDER, AIAUTO_MODEL, AIAUTO_LITELLM_MODEL, AIAUTO_LITELLM_BASE, AIAUTO_LITELLM_KEY,
AIAUTO_CLAUDE_CLI / AIAUTO_CURSOR_CLI / AIAUTO_COPILOT_CLI (binary paths).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any

log = logging.getLogger(__name__)

ORDER = ["claude-code", "cursor", "copilot", "anthropic", "litellm"]
LABELS = {
    "auto": "Auto (your subscription first, then API key, then LiteLLM)",
    "claude-code": "Claude subscription (Claude Code CLI)",
    "cursor": "Cursor subscription (Cursor CLI)",
    "copilot": "GitHub Copilot subscription (Copilot CLI)",
    "anthropic": "Anthropic API key",
    "litellm": "Any model via LiteLLM (OpenAI, Gemini, Azure, Bedrock, Ollama, proxy ...)",
}
DEFAULT_MODEL = "claude-opus-5-5"
CLI_TIMEOUT = int(os.getenv("AIAUTO_AI_TIMEOUT", "180"))

# settings pushed by the hub (web UI) override the environment; keys never leave the machine they were set on
_settings: dict[str, Any] = {}
_failed: dict[str, str] = {}            # provider -> why it was skipped (cleared when settings change)
_lock = threading.Lock()
_anthropic_client = None                # tests inject a mock client here


class ProviderError(Exception):
    """The provider could not answer. `fatal` = do not try it again (not installed / not logged in / no credits)."""

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


def configure(settings: dict[str, Any] | None) -> None:
    """Apply AI settings ({provider, model, anthropic_key, litellm_model, litellm_base, litellm_key, ...})."""
    global _anthropic_client
    with _lock:
        _settings.clear()
        _settings.update({k: v for k, v in (settings or {}).items() if v not in (None, "")})
        _failed.clear()
        _anthropic_client = None


def setting(key: str, env: str | None = None, default: Any = None) -> Any:
    return _settings.get(key) or (os.getenv(env) if env else None) or default


def chosen() -> str:
    p = (setting("provider", "AIAUTO_AI_PROVIDER", "auto") or "auto").lower()
    return p if p in ORDER or p == "auto" else "auto"


def model_for(name: str) -> str | None:
    """The model a provider is asked for. CLIs use the model you set, else their own default."""
    m = setting("model", "AIAUTO_MODEL")
    if name == "anthropic":
        return m or DEFAULT_MODEL
    if name == "litellm":
        return setting("litellm_model", "AIAUTO_LITELLM_MODEL") or (m if m and "/" in m else None)
    if name == "claude-code":
        return m or DEFAULT_MODEL
    return m          # cursor / copilot: their own default unless one is set ("gpt-5", "sonnet-4.5", ...)


# ----------------------------------------------------------------------------------- detection
def _cli(name: str) -> list[str] | None:
    """The command that starts a provider CLI. The setting may be a path, a command line ("npx …"), or a list."""
    env = {"claude-code": "AIAUTO_CLAUDE_CLI", "cursor": "AIAUTO_CURSOR_CLI", "copilot": "AIAUTO_COPILOT_CLI"}[name]
    exe = {"claude-code": "claude", "cursor": "cursor-agent", "copilot": "copilot"}[name]
    raw = setting(env.lower(), env)
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw] or None
    if raw:
        if os.path.exists(raw):
            return _unshim(raw)
        import shlex
        parts = shlex.split(raw, posix=os.name != "nt")
        return [p.strip('"') for p in parts] or None
    found = shutil.which(exe)
    return _unshim(found) if found else None


def _unshim(path: str) -> list[str]:
    """Windows: npm installs CLIs as .cmd shims. Arguments to a .cmd pass through cmd.exe, which mangles quotes,
    ">" and "&" in prompts and JSON schemas, so run the shim's node script directly when we can find it."""
    if os.name != "nt" or not path.lower().endswith((".cmd", ".bat")):
        return [path]
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return [path]
    m = re.search(r'%~?dp0%?\\?([^"%\r\n]+?\.(?:m?js|cjs))', text)
    node = shutil.which("node")
    if m and node:
        script = os.path.join(os.path.dirname(path), m.group(1).lstrip("\\"))
        if os.path.exists(script):
            return [node, script]
    return [path]


def _anthropic_cred() -> bool:
    if setting("anthropic_key"):
        return True
    return any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) or \
        os.path.isdir(os.path.expanduser("~/.config/anthropic"))


def detect(name: str) -> tuple[bool, str]:
    """(usable?, explanation) without calling the model."""
    if name in _failed:
        return False, _failed[name]
    if name in ("claude-code", "cursor", "copilot"):
        cmd = _cli(name)
        return (True, f"CLI found: {' '.join(cmd)}") if cmd else (False, "CLI not installed on this machine")
    if name == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "anthropic package not installed"
        return (True, "API key / profile found") if _anthropic_cred() or _anthropic_client else (False, "no API key")
    if name == "litellm":
        try:
            import litellm  # noqa: F401
        except ImportError:
            return False, "litellm not installed (pip install litellm)"
        return (True, f"model {model_for('litellm')}") if model_for("litellm") else (False, "no LiteLLM model set")
    return False, "unknown provider"


def candidates() -> list[str]:
    c = chosen()
    names = ORDER if c == "auto" else [c]
    return [n for n in names if detect(n)[0]]


def available() -> bool:
    if os.getenv("AIAUTO_DISABLE_LLM"):
        return False
    return bool(candidates())


def active() -> str | None:
    c = candidates()
    return c[0] if c else None


def status() -> str:
    if os.getenv("AIAUTO_DISABLE_LLM"):
        return "disabled by AIAUTO_DISABLE_LLM"
    a = active()
    if a:
        m = model_for(a)
        return f"enabled: {LABELS[a]}" + (f" · {m}" if m else "")
    c = chosen()
    why = "; ".join(f"{n}: {detect(n)[1]}" for n in (ORDER if c == "auto" else [c]))
    return f"disabled: {why}"


def report() -> list[dict]:
    """Every provider with its state (for the settings page)."""
    act = active()
    return [{"id": n, "label": LABELS[n], "usable": detect(n)[0], "detail": detect(n)[1], "model": model_for(n),
             "active": n == act} for n in ORDER]


# ----------------------------------------------------------------------------------- the one call
def call_json(system: str, user: str, schema: dict, image_png: bytes | None = None, max_tokens: int = 16000,
              effort: str = "low") -> dict | None:
    """Ask the active provider for JSON matching `schema`. Falls through to the next provider on failure."""
    if os.getenv("AIAUTO_DISABLE_LLM"):
        return None
    for name in candidates():
        try:
            out = _CALLS[name](system, user, schema, image_png, max_tokens, effort)
        except ProviderError as e:
            if e.fatal:
                with _lock:
                    _failed[name] = str(e)[:300]
                log.warning("AI provider %s unusable (%s); trying the next one", name, e)
                continue
            log.warning("AI provider %s failed: %s", name, e)
            return None            # transient: let the caller use its non-AI fallback for this step
        if out is not None:
            return out
        return None                # declined / no answer: same
    return None


def _instructions(system: str, schema: dict) -> str:
    return (system + "\n\nAnswer with ONLY a JSON object (no prose, no code fences) that matches this JSON Schema:\n"
            + json.dumps(schema))


def _first_json(text: str) -> dict | None:
    """The JSON object in a CLI's text answer (tolerates code fences and prose around it)."""
    if not text:
        return None
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    v = json.loads(text[start:i + 1])
                    if isinstance(v, dict):
                        return v
                except json.JSONDecodeError:
                    break
                break
        start = text.find("{", start + 1)
    return None


_FATAL = re.compile(r"not (logged|signed) in|login|log in|authenticat|unauthori[sz]ed|credits?|subscription|"
                    r"quota|billing|api key|forbidden|401|403|not installed|no such file", re.I)


def _run(cmd: list[str], stdin: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=CLI_TIMEOUT, cwd=cwd,
                              env={**os.environ, "NO_COLOR": "1"})
    except FileNotFoundError as e:
        raise ProviderError(f"{cmd[0]} not installed", fatal=True) from e
    except subprocess.TimeoutExpired as e:
        raise ProviderError(f"{cmd[0]} timed out after {CLI_TIMEOUT}s") from e


def _image_file(png: bytes | None, d: str) -> str | None:
    if not png:
        return None
    p = os.path.join(d, "screenshot.png")
    with open(p, "wb") as fh:
        fh.write(png)
    return p


# -- Claude subscription: Claude Code CLI, structured output enforced by --json-schema
def _claude_code(system, user, schema, png, max_tokens, effort):
    exe = _cli("claude-code")
    if not exe:
        raise ProviderError("Claude Code CLI not installed", fatal=True)
    with tempfile.TemporaryDirectory(prefix="aiauto-ai-") as d:
        img = _image_file(png, d)
        prompt = user + (f"\n\nThe screenshot is the image file {img} - read it." if img else "")
        cmd = [*exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", system,
               "--no-session-persistence", "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
               "--tools", "Read" if img else ""]
        if img:
            cmd += ["--allowedTools", "Read", "--add-dir", d]
        m = model_for("claude-code")
        if m:
            cmd += ["--model", m]
        r = _run(cmd, prompt, cwd=d)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        msg = (r.stderr or r.stdout or "").strip()[:300]
        raise ProviderError(f"claude CLI: {msg or 'no output'}", fatal=bool(_FATAL.search(msg))) from e
    if out.get("is_error"):
        msg = f"{out.get('api_error') or ''} {out.get('result') or ''}".strip()
        fatal = out.get("api_error_status") in (401, 403) or bool(_FATAL.search(msg))
        raise ProviderError(f"claude CLI: {msg[:300]}", fatal=fatal)
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    return _first_json(out.get("result") or "")


# -- Cursor subscription: Cursor CLI headless mode, JSON envelope with the answer in "result"
def _cursor(system, user, schema, png, max_tokens, effort):
    exe = _cli("cursor")
    if not exe:
        raise ProviderError("Cursor CLI not installed", fatal=True)
    with tempfile.TemporaryDirectory(prefix="aiauto-ai-") as d:
        img = _image_file(png, d)
        prompt = _instructions(system, schema) + "\n\n" + user + (f"\n\nThe screenshot is the image file {img}." if img else "")
        cmd = [*exe, "-p", "--output-format", "json"]
        m = model_for("cursor")
        if m:
            cmd += ["--model", m]
        r = _run(cmd + [prompt], "", cwd=d)
    text = r.stdout.strip()
    if r.returncode != 0 and not text:
        msg = (r.stderr or "").strip()[:300]
        raise ProviderError(f"cursor-agent: {msg or f'exit {r.returncode}'}", fatal=bool(_FATAL.search(msg)))
    try:
        env = json.loads(text)
        if isinstance(env, dict) and env.get("is_error"):
            msg = str(env.get("result") or env.get("error") or "")[:300]
            raise ProviderError(f"cursor-agent: {msg}", fatal=bool(_FATAL.search(msg)))
        text = env.get("result", text) if isinstance(env, dict) else text
    except json.JSONDecodeError:
        pass
    return _first_json(text if isinstance(text, str) else json.dumps(text))


# -- GitHub Copilot subscription: Copilot CLI programmatic mode, plain-text answer
def _copilot(system, user, schema, png, max_tokens, effort):
    exe = _cli("copilot")
    if not exe:
        raise ProviderError("Copilot CLI not installed", fatal=True)
    with tempfile.TemporaryDirectory(prefix="aiauto-ai-") as d:
        img = _image_file(png, d)
        prompt = _instructions(system, schema) + "\n\n" + user + (f"\n\nThe screenshot is the image file {img}." if img else "")
        cmd = [*exe, "-p", prompt]
        m = model_for("copilot")
        if m:
            cmd += ["--model", m]
        r = _run(cmd, "", cwd=d)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()[:300]
        raise ProviderError(f"copilot: {msg or f'exit {r.returncode}'}", fatal=bool(_FATAL.search(msg)))
    return _first_json(r.stdout)


# -- Anthropic API key: SDK structured outputs, server-side refusal fallback
def _anthropic(system, user, schema, png, max_tokens, effort):
    global _anthropic_client
    import anthropic
    if _anthropic_client is None:
        key = setting("anthropic_key")
        _anthropic_client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    content: Any = user
    if png:
        import base64
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}},
                   {"type": "text", "text": user}]
    try:
        resp = _anthropic_client.beta.messages.create(
            model=model_for("anthropic"), max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            # if the primary model declines, the API re-runs the request on a fallback model
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise ProviderError(f"Anthropic: {e}", fatal=True) from e
    except anthropic.NotFoundError as e:
        raise ProviderError(f"Anthropic: model not found ({model_for('anthropic')})", fatal=True) from e
    except anthropic.RateLimitError as e:
        raise ProviderError(f"Anthropic rate limited: {e}") from e
    except anthropic.APIStatusError as e:
        raise ProviderError(f"Anthropic error {e.status_code}: {e}") from e
    except anthropic.APIConnectionError as e:
        raise ProviderError(f"Anthropic unreachable: {e}") from e
    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("Anthropic stop_reason=%s", resp.stop_reason)
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


# -- any model through LiteLLM (also a LiteLLM proxy / any OpenAI-compatible endpoint via litellm_base)
def _litellm(system, user, schema, png, max_tokens, effort):
    try:
        import litellm
    except ImportError as e:
        raise ProviderError("litellm not installed", fatal=True) from e
    model = model_for("litellm")
    if not model:
        raise ProviderError("no LiteLLM model set", fatal=True)
    content: Any = user
    if png:
        import base64
        content = [{"type": "text", "text": user},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]
    kw: dict[str, Any] = {"model": model, "max_tokens": min(max_tokens, 8000),
                          "messages": [{"role": "system", "content": _instructions(system, schema)},
                                       {"role": "user", "content": content}]}
    if setting("litellm_base", "AIAUTO_LITELLM_BASE"):
        kw["api_base"] = setting("litellm_base", "AIAUTO_LITELLM_BASE")
    if setting("litellm_key", "AIAUTO_LITELLM_KEY"):
        kw["api_key"] = setting("litellm_key", "AIAUTO_LITELLM_KEY")
    if os.getenv("AIAUTO_LITELLM_MOCK"):           # tests: no network
        kw["mock_response"] = os.environ["AIAUTO_LITELLM_MOCK"]
    try:
        if litellm.supports_response_schema(model=model):
            kw["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}}
    except Exception:
        pass
    try:
        resp = litellm.completion(**kw)
    except Exception as e:     # litellm maps every provider's errors onto OpenAI-style exception classes
        name = type(e).__name__
        fatal = name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError", "BadRequestError") or \
            bool(_FATAL.search(str(e)[:300]))
        raise ProviderError(f"LiteLLM ({model}): {name}: {str(e)[:300]}", fatal=fatal) from e
    return _first_json(resp.choices[0].message.content or "")


_CALLS = {"claude-code": _claude_code, "cursor": _cursor, "copilot": _copilot, "anthropic": _anthropic, "litellm": _litellm}
