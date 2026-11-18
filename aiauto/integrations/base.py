"""Shared pieces for the native connectors: an HTTP client with rate-limit back-off, the issue model, text
clean-up, and the connector interface. Token sign-in only (API tokens / personal access tokens / API keys)."""
from __future__ import annotations

import base64
import html
import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


class IntegrationError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Http:
    """httpx with retries on 429 / 502 / 503 / 504 (honours Retry-After, exponential back-off with jitter).
    Error messages never contain the credentials."""

    def __init__(self, base_url: str, headers: dict[str, str], name: str, timeout: float = 30, verify: bool | str = True):
        self.base = base_url.rstrip("/")
        self.name = name
        self.client = httpx.Client(headers={"Accept": "application/json", "User-Agent": "autopilot-tests", **headers},
                                   timeout=timeout, verify=verify, follow_redirects=True)

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}{path if path.startswith('/') else '/' + path}"

    def request(self, method: str, path: str, *, ok=(200, 201, 202, 204), retries: int = 4, **kw) -> httpx.Response:
        for attempt in range(retries + 1):
            try:
                r = self.client.request(method, self.url(path), **kw)
            except httpx.HTTPError as e:
                if attempt < retries:
                    time.sleep(min(8, 0.5 * 2 ** attempt) + random.random() * 0.2)
                    continue
                raise IntegrationError(f"{self.name}: cannot reach {self.base} ({type(e).__name__})") from e
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                wait = r.headers.get("retry-after")
                time.sleep(min(30, float(wait)) if wait and wait.replace(".", "", 1).isdigit()
                           else min(8, 0.5 * 2 ** attempt) + random.random() * 0.3)
                continue
            if r.status_code not in ok:
                detail = r.text[:300].replace("\n", " ")
                hint = {401: " (check the token)", 403: " (the token lacks permission)", 404: " (not found)"}.get(r.status_code, "")
                raise IntegrationError(f"{self.name}: {method} {path.split('?')[0]} -> {r.status_code}{hint}: {detail}", r.status_code)
            return r
        raise IntegrationError(f"{self.name}: gave up after retries")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def json(self, method: str, path: str, **kw) -> Any:
        r = self.request(method, path, **kw)
        return r.json() if r.content else None


def basic(user: str, secret: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Issue:
    """A story / bug / work item, normalised across trackers."""
    key: str
    title: str
    id: str = ""
    type: str = ""
    status: str = ""
    url: str = ""
    description: str = ""
    acceptance: list[str] = field(default_factory=list)
    steps_to_reproduce: list[str] = field(default_factory=list)
    expected_result: str = ""
    actual_result: str = ""
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)          # {key, title, relation}
    remote_links: list[dict] = field(default_factory=list)   # {url, title}
    comments: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------------ text clean-up
def html_to_text(s: str | None) -> str:
    """Confluence storage / Azure DevOps HTML -> readable text (headings and list items on their own lines,
    table cells separated by |)."""
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|table|ul|ol)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", s)
    s = re.sub(r"(?i)</t[dh]>", " | ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def wiki_to_text(s: str | None) -> str:
    """Jira wiki markup (API v2 descriptions) -> text, keeping headings as '# ' and list markers."""
    if not s:
        return ""
    s = re.sub(r"\{(code|noformat)(:[^}]*)?\}(.*?)\{\1\}", lambda m: m.group(3), s, flags=re.S)
    s = re.sub(r"(?m)^\s*#+\s+", "1. ", s)                       # wiki numbered list: "# item" (before headings!)
    s = re.sub(r"(?m)^\s*\*+\s+", "- ", s)                        # wiki bullets: "* item"
    s = re.sub(r"(?m)^h([1-6])\.\s*", lambda m: "#" * int(m.group(1)) + " ", s)
    s = re.sub(r"\[([^|\]]+)\|([^\]]+)\]", r"\1 (\2)", s)       # [text|url]
    s = re.sub(r"\{color[^}]*\}|\{panel[^}]*\}", "", s)
    s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", s)          # *bold*
    s = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", s)            # _italic_
    return s.strip()


HEADING = re.compile(r"^\s*(?:#{1,6}\s+|h[1-6]\.\s*|\*{1,2}|__)?\s*(?P<h>[A-Za-z][\w /&-]{2,40}?)\s*:?\s*(?:\*{1,2}|__)?\s*:?\s*$")


def sections(text: str) -> dict[str, list[str]]:
    """Split text into {heading (lower case): lines}. Lines before any heading go under ''."""
    out: dict[str, list[str]] = {"": []}
    cur = ""
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = HEADING.match(line)
        is_heading = bool(m) and (line.lstrip().startswith(("#", "h1.", "h2.", "h3.", "h4.", "*", "_")) or line.rstrip().endswith(":"))
        if is_heading and len(line.strip()) <= 48:
            cur = m.group("h").strip().lower()
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(line)
    return out


LIST_ITEM = re.compile(r"^\s*(?:[-*•#]+|\d+[.)]|\(\w\))\s+")


def items(lines: list[str]) -> list[str]:
    """Bulleted / numbered lines -> items (continuation lines joined)."""
    out: list[str] = []
    for line in lines:
        if LIST_ITEM.match(line) or not out:
            out.append(LIST_ITEM.sub("", line).strip())
        else:
            out[-1] = f"{out[-1]} {line.strip()}"
    return [x for x in out if x]


def find_section(secs: dict[str, list[str]], *names: str) -> list[str]:
    """The first section whose heading has one of these names as whole words (in the order the names are given)."""
    for n in names:
        rx = re.compile(rf"(?<![a-z]){re.escape(n)}(?![a-z])")
        for h, lines in secs.items():
            if h and rx.search(h):
                return lines
    return []


SECRET_RX = re.compile(r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
                       r"-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----|ATATT[A-Za-z0-9_=-]{20,}|"
                       r"(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"]{6,})")


def scrub(text: str) -> str:
    """Remove things that look like credentials before text is shown to an AI provider or stored in a pack."""
    return SECRET_RX.sub("[redacted]", text or "")


class Connector:
    """One tool. Subclasses implement the capabilities their `roles` promise."""
    kind = ""
    label = ""
    roles: tuple[str, ...] = ()           # tracker | docs | code | tests
    status = "standard"                   # standard | under development | planned

    def __init__(self, settings: dict, secrets: dict):
        self.s = settings or {}
        self.secrets = secrets or {}

    # every connector
    def test(self) -> str:                # who am I / what can I see
        raise NotImplementedError

    # tracker
    def search(self, query: str, limit: int = 25) -> list[Issue]:
        raise NotImplementedError

    def get_issue(self, key: str) -> Issue:
        raise NotImplementedError

    def comment(self, key: str, text: str) -> None:
        raise NotImplementedError

    def transition(self, key: str, to_status: str) -> str:
        raise NotImplementedError

    def create_bug(self, summary: str, description: str, story: Issue, labels: list[str]) -> str:
        raise NotImplementedError

    def find_bug(self, label: str) -> str | None:
        return None

    def attach(self, key: str, filename: str, data: bytes) -> None:
        return None

    # docs
    def pages_for(self, issue: Issue, limit: int = 5) -> list[dict]:
        raise NotImplementedError

    # code
    def context_for(self, issue: Issue) -> dict:
        raise NotImplementedError

    # tests
    def push_case(self, case: dict, issue: Issue) -> str:
        raise NotImplementedError

    def start_cycle(self, name: str, issue: Issue) -> str:
        raise NotImplementedError

    def record_execution(self, cycle: str, case: dict, result: dict, issue: Issue) -> str:
        raise NotImplementedError
