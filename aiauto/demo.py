"""Demo data: a ready-to-explore hub database built from real runs.

    python -m aiauto demo                 # fills ./aiauto.db and ./artifacts (the hub's defaults)
    python -m aiauto demo --db demo.db --artifacts demo_artifacts --port 8000

It starts a hub on that database, creates everything through the same REST API the web app uses, waits for the
runs to finish and stops the hub again. Nothing is made up: every run really executed against the practice pages the
hub serves at /demo/ (examples/), so its report, video, screenshots and generated scripts are real, and replays
work whenever the hub runs on the same port.

What you get (see docs/getting-started.md):
  * accounts: admin / tester / viewer (one per role), password `autopilot-demo` unless --password
  * projects: Default and "Demo shop" (tester is a member)
  * Demo shop: the shop cart test, 2 iterations x Playwright + Selenium; a Script-mode replay of it;
    a login test with a masked password; the 52-step element playground; two library scripts
  * Default: a test design written from a pasted user story (sign-up form) with its cases run; the form has a
    planted bug, so one case fails, as a real regression would
AI is off while loading (the built-in rules parse the tests), so it needs no AI account and gives the same result
every time.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PASSWORD = "autopilot-demo"
LIVE = ("queued", "dispatched", "running")

SIGNUP_STORY = """# Sign up form

As a visitor I want to create an account on the sign up page at /signup.html.

## Acceptance criteria
- Given I am on the sign up page, when I fill valid details and click Create account then I see "Account created"
- Email is required and must be a valid email address; shows "Enter a valid email"
- Age must be between 18 and 99; shows "Age must be 18-99"
"""


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _start_hub(db: Path, artifacts: Path, port: int, password: str) -> subprocess.Popen:
    env = {**os.environ, "AIAUTO_DB": str(db), "AIAUTO_ARTIFACTS": str(artifacts), "AIAUTO_AUTH": "on",
           "AIAUTO_ADMIN_USER": "admin", "AIAUTO_ADMIN_PASSWORD": password, "AIAUTO_DISABLE_LLM": "1",
           "AIAUTO_DEMO_SITE": "on", "AIAUTO_LOCAL_WORKERS": os.getenv("AIAUTO_LOCAL_WORKERS", "2")}
    log = open(db.with_suffix(".demo.log"), "w", encoding="utf-8")
    return subprocess.Popen([sys.executable, "-m", "aiauto", "server", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)


def _wait_up(base: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise SystemExit("The hub stopped while starting; see the .demo.log file next to the database")
        try:
            if httpx.get(base + "/api/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("The hub did not start in time")


def _signup_repo(path: Path) -> Path | None:
    """A small Git repository holding the sign-up page, committed as 'DEMO-1: sign up form'."""
    import shutil
    if not shutil.which("git"):
        return None
    if (path / ".git").is_dir():
        return path
    (path / "web").mkdir(parents=True, exist_ok=True)
    (path / "web" / "signup.html").write_text((EXAMPLES / "signup" / "signup.html").read_text(encoding="utf-8"), encoding="utf-8")
    g = ["git", "-C", str(path), "-c", "user.name=Autopilot demo", "-c", "user.email=demo@example.invalid"]
    try:
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(g + ["add", "web/signup.html"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "DEMO-1: sign up form"], check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return path


class _Api:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=120, headers={"X-Requested-With": "autopilot"})

    def __call__(self, method: str, path: str, project: str | None = None, **kw):
        headers = {"X-Project": project} if project else {}
        r = self.c.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()


def _wait_runs(api: _Api, ids: list[str], timeout: float = 1200) -> dict[str, dict]:
    end = time.time() + timeout
    while True:
        rows = {r["run_id"]: r for p in ("default", "shop") for r in api("GET", "/api/runs", project=p, params={"limit": 1000})}
        left = [i for i in ids if rows.get(i, {}).get("status") in LIVE or i not in rows]
        if not left or time.time() > end:
            return rows
        time.sleep(2)


def build(db: Path, artifacts: Path, port: int, password: str, engines: list[str]) -> dict:
    base = f"http://localhost:{port}"
    site = f"{base}/demo"
    proc = _start_hub(db, artifacts, port, password)
    try:
        _wait_up(base, proc)
        api = _Api(base)
        r = api.c.post("/api/auth/login", json={"username": "admin", "password": password})
        if r.status_code != 200:
            raise SystemExit("Could not sign in as admin. With --force on a database that has accounts, the admin must be "
                             "called 'admin' and --password must be its password.")
        have = {u["username"] for u in api("GET", "/api/users")}
        for user, role, name in (("tester", "tester", "Tess Tester"), ("viewer", "viewer", "Vic Viewer")):
            if user not in have:
                api("POST", "/api/users", json={"username": user, "role": role, "name": name, "password": password})
        api("PUT", "/api/projects/shop", json={"name": "Demo shop", "description": "Practice shop at /demo/shop",
                                               "members": ["tester"]})
        quick = {"timeout_ms": 8000}
        print("demo: running the tests (this takes a few minutes) ...")

        # Demo shop: data-driven cart test, 2 iterations, on each engine
        cart = api("POST", "/api/runs", project="shop", json={
            "text": (EXAMPLES / "shop_cart.txt").read_text(encoding="utf-8"), "name": "Shop: search, add to cart, check the cart",
            "data": {"base_url": f"{site}/shop"},
            "iterations": [{"data": {"product": "wireless mouse", "brand": "Logitech", "qty": "2"}},
                           {"data": {"product": "wireless mouse", "brand": "HP", "qty": "1"}}],
            "engines": engines, "config": quick})["run_ids"]
        login_text = (EXAMPLES / "login_test.txt").read_text(encoding="utf-8").replace("file://EXAMPLES_DIR", "${base_url}")
        login = api("POST", "/api/runs", project="shop", json={
            "text": login_text, "name": "Demo app: log in and open Laptops",
            "data": {"base_url": site, "user": {"name": "alice", "password": "secret"}, "country": "India"},   # the demo app's password
            "sensitive": ["user.password"], "config": quick})["run_ids"]
        files = db.parent / "demo_files"            # the playground uploads a file
        files.mkdir(parents=True, exist_ok=True)
        (files / "resume.txt").write_text("Asha Rao - QA engineer\n", encoding="utf-8")
        play_text = (EXAMPLES / "playground_test.txt").read_text(encoding="utf-8") \
            .replace("PLAYGROUND_URL", f"{site}/playground.html").replace("UPLOAD_FILE", str(files / "resume.txt"))
        play = api("POST", "/api/runs", project="shop", json={
            "text": play_text, "name": "Element playground: shadow roots, iframes, dialogs, drag and drop",
            "data": json.loads((EXAMPLES / "playground_data.json").read_text(encoding="utf-8")), "config": quick})["run_ids"]

        # Default project: a test design from a pasted story, every case approved and run. The form's fields come from
        # its code, as for a real team: a Local Git repository (Admin -> Integrations) with a commit naming DEMO-1
        repo = _signup_repo(db.parent / "demo_repo")
        if repo:
            api("PUT", "/api/connections/local_repo", json={"settings": {"path": str(repo)}, "secrets": {}})
        else:
            print("demo: git not found - the sign-up design has no code to read its fields from, so its cases will fail")
        design = api("POST", "/api/designs", json={"story": {"text": SIGNUP_STORY, "key": "DEMO-1"}})
        api("POST", f"/api/designs/{design['id']}/status", json={"case_ids": [c["id"] for c in design["cases"]], "status": "approved"})
        designed = api("POST", f"/api/designs/{design['id']}/run", json={
            "base_url": f"{site}/signup", "config": {**quick, "record_video": True}})["run_ids"]

        rows = _wait_runs(api, cart + login + play + designed)
        # a Script-mode replay of the first cart run, and the library
        first = next((r for r in cart if rows.get(r, {}).get("status") == "passed"), None)
        replay = []
        if first:
            replay = api("POST", "/api/runs", project="shop", json={"replay_run_id": first, "config": quick})["run_ids"]
            api("POST", "/api/library/from-run", project="shop", json={"run_id": first, "name": "shop-cart"})
        ok_login = next((r for r in login if rows.get(r, {}).get("status") == "passed"), None)
        if ok_login:
            api("POST", "/api/library/from-run", project="shop", json={"run_id": ok_login, "name": "demo-login"})
        rows = _wait_runs(api, replay)
        every = cart + login + play + designed + replay
        return {"runs": {i: rows.get(i, {}).get("status") for i in every}, "design": design["id"], "base": base}
    finally:
        proc.terminate()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m aiauto demo", description="Fill a hub database with demo data from real runs.")
    ap.add_argument("--db", default=os.getenv("AIAUTO_DB", "aiauto.db"), help="database file (default: the hub's, ./aiauto.db)")
    ap.add_argument("--artifacts", default=os.getenv("AIAUTO_ARTIFACTS", "artifacts"), help="run folders (default ./artifacts)")
    ap.add_argument("--port", type=int, default=8000, help="the port you will run the hub on (demo runs point at it)")
    ap.add_argument("--password", default=PASSWORD, help=f"password for the demo accounts (default {PASSWORD})")
    ap.add_argument("--engines", default="playwright,selenium", help="engines for the cart test (default both)")
    ap.add_argument("--force", action="store_true", help="add the demo to a database that already has accounts")
    a = ap.parse_args(argv)
    db, artifacts = Path(a.db).resolve(), Path(a.artifacts).resolve()
    if len(a.password) < 10:
        raise SystemExit("--password: at least 10 characters")
    if db.exists() and not a.force:
        import sqlite3
        with sqlite3.connect(db) as c:
            has = c.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone() and \
                c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if has:
            raise SystemExit(f"{db} already has accounts. Use another file (--db demo.db --artifacts demo_artifacts) "
                             "or --force to add the demo users and runs to it.")
    if not _free(a.port):
        raise SystemExit(f"Port {a.port} is in use (is a hub running?). Stop it first, or pass --port.")
    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    print(f"demo: database {db}\ndemo: artifacts {artifacts}")
    got = build(db, artifacts, a.port, a.password, engines)
    counts: dict[str, int] = {}
    for st in got["runs"].values():
        counts[st or "missing"] = counts.get(st or "missing", 0) + 1
    print("demo: runs " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    env = "" if str(db) == str(Path("aiauto.db").resolve()) else f"AIAUTO_DB={a.db} AIAUTO_ARTIFACTS={a.artifacts} "
    print(f"""
Done. Start the hub and sign in:

  {env}python -m aiauto server --port {a.port}
  open {got['base']}   ->  admin / {a.password}   (also: tester, viewer; same password)

Change these passwords before you share the hub (Admin -> Users). The practice pages are at {got['base']}/demo/.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
