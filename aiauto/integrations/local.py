"""A Git clone on the hub machine as the code source: no token here — `git` uses the credentials already set up
for that clone (credential helper, SSH key), and only when "fetch first" is on. Reads commits that mention the
story key (any branch), their diffs and the UI files they touch; without a key, the UI files that match the
story's words. Files are read only from inside the repository folder."""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

from .base import Connector, Issue, IntegrationError
from .github import MAX_DIFF, MAX_FILE, UI_FILE, _dedupe, buttons_in, fields_in, routes_in

KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
MAX_FILES = 6
STOP = {"the", "and", "for", "with", "that", "this", "from", "when", "user", "page", "should", "able", "want", "can"}


class LocalRepo(Connector):
    kind, label, roles, status = "local_repo", "Local Git repository", ("code",), "standard"

    def __init__(self, settings, secrets):
        super().__init__(settings, secrets)
        raw = (self.s.get("path") or "").strip()
        if not raw:
            raise IntegrationError("Local repository: set the folder of the clone (on the machine the hub runs on)")
        p = Path(raw).expanduser()
        if not p.is_dir():
            raise IntegrationError(f"Local repository: {raw} is not a folder on the hub machine"
                                   " (in Docker, mount it into the hub container first)")
        self.root = p.resolve()
        self.globs = [g.strip() for g in (self.s.get("ui_paths") or "").split(",") if g.strip()]
        self.fetch = str(self.s.get("fetch") or "no").lower() == "yes"

    def _git(self, *args: str, timeout: int = 60) -> str:
        try:
            r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise IntegrationError("Local repository: git is not installed on the hub machine")
        except subprocess.TimeoutExpired:
            raise IntegrationError(f"Local repository: git {args[0]} took too long")
        if r.returncode:
            raise IntegrationError(f"Local repository: git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def test(self) -> str:
        top = self._git("rev-parse", "--show-toplevel").strip()
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        last = self._git("log", "-1", "--format=%h %s").strip()
        out = f"{top} on {branch} (last commit {last[:80]})"
        if self.fetch:
            self._git("fetch", "--all", "--prune", timeout=180)
            out += "; fetch works"
        return out

    def _read(self, path: str, rev: str | None = None) -> str:
        f = (self.root / path).resolve()
        if not f.is_relative_to(self.root):
            return ""                                    # never outside the repository
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8", errors="replace")[:MAX_FILE]
            except OSError:
                return ""
        if rev:                                          # changed on another branch: read it from the commit
            try:
                return self._git("show", f"{rev}:{path}")[:MAX_FILE]
            except IntegrationError:
                return ""
        return ""

    def _ui_files(self) -> list[str]:
        files = self._git("ls-files").splitlines()
        if self.globs:
            files = [f for f in files if any(fnmatch.fnmatch(f, g) for g in self.globs)]
        return [f for f in files if UI_FILE.search(f) and "node_modules/" not in f][:5000]

    def context_for(self, issue: Issue) -> dict:
        out = {"prs": [], "diff": "", "files": [], "fields": [], "buttons": [], "pages": [], "routes": [], "warnings": []}
        if self.fetch:
            try:
                self._git("fetch", "--all", "--prune", timeout=180)
            except IntegrationError as e:
                out["warnings"].append(f"{e} (reading the clone as it is)")
        diff, picked = [], {}
        if KEY.match(issue.key or ""):
            log = self._git("log", "--all", "-i", f"--grep={issue.key}", "-n", "20", "--format=%H%x09%h%x09%s")
            for line in log.splitlines():
                full, short, subject = (line.split("\t") + ["", ""])[:3]
                names = self._git("show", "--name-status", "--format=", full).splitlines()
                files = [n.split("\t")[-1] for n in names if n and not n.startswith("D")]
                out["prs"].append({"repo": self.root.name, "number": short, "title": subject, "url": "", "kind": "commit", "files": files})
                if sum(len(d) for d in diff) < MAX_DIFF:
                    diff.append(self._git("show", "--format=commit %h %s", "-U3", full)[:MAX_DIFF // 2])
                for f in files:
                    if UI_FILE.search(f) and f not in picked and len(picked) < MAX_FILES:
                        picked[f] = full
        if not picked:   # nothing mentions the key: the UI files that share the most words with the story
            words = {w for w in re.findall(r"[a-z]{4,}", f"{issue.title} {' '.join(issue.acceptance)}".lower()) if w not in STOP}
            scored = []
            for f in self._ui_files():
                name = f.lower()
                score = sum(3 for w in words if w in name)
                if score or len(scored) < 400:
                    text = self._read(f).lower()
                    score += sum(1 for w in words if w in text)
                if score:
                    scored.append((score, f))
            for _, f in sorted(scored, reverse=True)[:MAX_FILES]:
                picked[f] = None
            if picked:
                out["warnings"].append("No commit mentions the story key: using the UI files that match its words (" +
                                       ", ".join(picked) + "). Check they are the right ones.")
        for f, rev in picked.items():
            text = self._read(f, rev)
            if not text:
                continue
            out["files"].append({"path": f, "text": text})
            if re.search(r"\.html?$", f, re.I):
                out["pages"].append(f.split("/")[-1])
        out["diff"] = "\n".join(diff)[:MAX_DIFF]
        for f in out["files"]:
            out["fields"] += fields_in(f["text"])
            out["buttons"] += buttons_in(f["text"])
            out["routes"] += routes_in(f["text"])
        out["routes"] += routes_in(out["diff"])
        out["fields"] = _dedupe(out["fields"], "name")
        out["buttons"] = list(dict.fromkeys(out["buttons"]))
        out["routes"] = list(dict.fromkeys(out["routes"]))
        return out
