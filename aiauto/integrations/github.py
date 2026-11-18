"""GitHub (github.com or GitHub Enterprise) with a personal access token. Finds the pull requests that mention a
story's key, reads their diff and changed files, and pulls out what matters for testing: form fields with their
rules (required, max length, format, min/max), buttons, page paths and routes."""
from __future__ import annotations

import base64
import re

from .base import Connector, Http, IntegrationError, Issue, bearer

MAX_DIFF = 20000
MAX_FILE = 30000
UI_FILE = re.compile(r"\.(html?|jsx|tsx|vue|svelte|erb|jinja2?|j2|hbs|cshtml|razor|php|astro)$|templates?/", re.I)


class GitHub(Connector):
    kind, label, roles, status = "github", "GitHub", ("code",), "standard"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        token = self.secrets.get("token") or ""
        if not token:
            raise IntegrationError("GitHub: add a personal access token (read access to contents and pull requests)")
        base = (self.s.get("base_url") or "https://api.github.com").rstrip("/")
        self.http = Http(base, {**bearer(token), "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, "GitHub")
        self.repos = [r.strip() for r in (self.s.get("repos") or "").split(",") if r.strip()]

    def test(self) -> str:
        me = self.http.json("GET", "/user")
        seen = []
        for r in self.repos:
            info = self.http.json("GET", f"/repos/{r}")
            seen.append(info.get("full_name", r))
        return f"signed in as {me.get('login')}" + (f"; repos {', '.join(seen)}" if seen else "")

    def _prs(self, repo: str, key: str) -> list[int]:
        nums: list[int] = []
        try:
            res = self.http.json("GET", "/search/issues", params={"q": f'"{key}" repo:{repo} is:pr', "per_page": 5})
            nums = [it["number"] for it in res.get("items", [])]
        except IntegrationError:
            pass
        if not nums:      # search disabled or not indexed yet: scan recent pull requests
            for pr in self.http.json("GET", f"/repos/{repo}/pulls", params={"state": "all", "per_page": 50}) or []:
                hay = " ".join([pr.get("title") or "", pr.get("body") or "", (pr.get("head") or {}).get("ref") or ""])
                if key.lower() in hay.lower():
                    nums.append(pr["number"])
        return nums[:3]

    def _file(self, repo: str, path: str, ref: str) -> str:
        data = self.http.json("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref})
        if isinstance(data, dict) and data.get("encoding") == "base64":
            return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")[:MAX_FILE]
        return ""

    def context_for(self, issue: Issue) -> dict:
        out = {"prs": [], "diff": "", "files": [], "fields": [], "buttons": [], "pages": [], "routes": []}
        diff_parts = []
        for repo in self.repos:
            for n in self._prs(repo, issue.key):
                pr = self.http.json("GET", f"/repos/{repo}/pulls/{n}")
                files = self.http.json("GET", f"/repos/{repo}/pulls/{n}/files", params={"per_page": 100}) or []
                sha = (pr.get("head") or {}).get("sha") or "HEAD"
                out["prs"].append({"repo": repo, "number": n, "title": pr.get("title", ""), "url": pr.get("html_url", ""),
                                   "files": [f["filename"] for f in files]})
                for f in files:
                    if f.get("patch"):
                        diff_parts.append(f"--- {f['filename']}\n{f['patch']}")
                    if f.get("status") != "removed" and UI_FILE.search(f["filename"]) and len(out["files"]) < 6:
                        try:
                            text = self._file(repo, f["filename"], sha)
                        except IntegrationError:
                            continue
                        out["files"].append({"path": f["filename"], "text": text})
                        if re.search(r"\.html?$", f["filename"], re.I):
                            out["pages"].append(f["filename"].split("/")[-1])
        out["diff"] = "\n".join(diff_parts)[:MAX_DIFF]
        for f in out["files"]:
            out["fields"] += fields_in(f["text"])
            out["buttons"] += buttons_in(f["text"])
            out["routes"] += routes_in(f["text"])
        out["routes"] += routes_in(out["diff"])
        out["fields"] = _dedupe(out["fields"], "name")
        out["buttons"] = list(dict.fromkeys(out["buttons"]))
        out["routes"] = list(dict.fromkeys(out["routes"]))
        return out


# --------------------------------------------------------------------------------- what code says about a form
ATTR = re.compile(r"""([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|\{([^}]*)\}|([^\s>]+)))?""")


def _attrs(tag: str) -> dict:
    out = {}
    for m in ATTR.finditer(tag):
        k = m.group(1).lower()
        v = next((g for g in m.groups()[1:] if g is not None), "")
        out[k] = v.strip("'\"") if v else True
    return out


def fields_in(text: str) -> list[dict]:
    """<input>/<textarea>/<select> with their label and rules, in page order."""
    labels = {}
    for m in re.finditer(r"(?is)<label[^>]*\bfor\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</label>", text):
        labels[m.group(1)] = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()
    out = []
    for m in re.finditer(r"(?is)<(input|textarea|select)\b([^>]*)>", text):
        a = _attrs(m.group(2))
        typ = (a.get("type") or ("textarea" if m.group(1).lower() == "textarea" else "select" if m.group(1).lower() == "select" else "text"))
        typ = str(typ).lower()
        if typ in ("hidden", "submit", "button", "reset", "image", "checkbox", "radio", "file"):
            continue
        fid = str(a.get("id") or "")
        label = labels.get(fid) or a.get("aria-label") or a.get("placeholder") or a.get("name") or fid
        if not label:
            continue
        rule = {"name": str(label).strip(), "id": fid, "attr_name": str(a.get("name") or ""), "type": typ,
                "required": "required" in a or str(a.get("aria-required", "")).lower() == "true"}
        for k in ("maxlength", "minlength", "min", "max", "pattern"):
            if a.get(k) not in (None, True, ""):
                rule[k] = str(a[k])
        out.append(rule)
    # schema validators (zod / yup): field: z.string().min(8).max(20).email()
    for m in re.finditer(r"(\w+)\s*:\s*(?:z|yup)\.(string|number)\(\)((?:\.\w+\([^)]*\))*)", text):
        chain = m.group(3)
        rule = {"name": m.group(1), "type": "email" if ".email(" in chain else ("number" if m.group(2) == "number" else "text"),
                "required": bool(re.search(r"\.(?:required|nonempty)\(|\.min\(1\)", chain))}
        mn, mx = re.search(r"\.min\((\d+)", chain), re.search(r"\.max\((\d+)", chain)
        key = ("min", "max") if m.group(2) == "number" else ("minlength", "maxlength")
        if mn:
            rule[key[0]] = mn.group(1)
        if mx:
            rule[key[1]] = mx.group(1)
        out.append(rule)
    return out


def buttons_in(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"(?is)<button\b([^>]*)>(.*?)</button>", text):
        words = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()
        if words:
            out.append(words)
    for m in re.finditer(r"(?is)<input\b([^>]*type\s*=\s*[\"']submit[\"'][^>]*)>", text):
        v = _attrs(m.group(1)).get("value")
        if v:
            out.append(str(v))
    return out


def routes_in(text: str) -> list[str]:
    out = []
    for rx in (r"""<Route[^>]*\bpath\s*=\s*["']([^"']+)""", r"""\bpath\s*:\s*["'](/[\w/:-]*)["']""",
               r"""@(?:app|router|bp|blueprint)\.(?:get|post|route|put)\(\s*["'](/[\w/<>:{}-]*)["']""",
               r"""\b(?:app|router)\.(?:get|post|use)\(\s*["'](/[\w/:-]*)["']"""):
        out += re.findall(rx, text)
    return out


def _dedupe(rows: list[dict], key: str) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        k = (r.get(key) or "").lower()
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out
