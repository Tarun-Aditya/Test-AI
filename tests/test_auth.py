"""Accounts on a shared hub: first-admin setup, sign-in, roles, ownership, private saved logins, each user's own
AI subscription (their token, isolated, encrypted at rest), and company SSO (OpenID Connect)."""
import base64
import importlib
import json
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from .conftest import EXAMPLES

W = {"X-Requested-With": "autopilot"}          # the web app sends this on every write (CSRF guard)
PW = "correct horse battery"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAUTO_AUTH", "on")
    monkeypatch.setenv("AIAUTO_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("AIAUTO_ARTIFACTS", str(tmp_path / "artifacts"))
    import aiauto.server.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        yield c, appmod


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app)


def _login(appmod, username, password=PW):
    c = _client(appmod)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _setup_admin(c, appmod):
    r = c.post("/api/auth/setup", json={"code": appmod.AUTH.setup_code, "username": "admin", "password": PW}, headers=W)
    assert r.status_code == 200, r.text
    return c


def test_setup_login_roles_and_csrf(hub):
    c, appmod = hub
    me = c.get("/api/auth/me").json()
    assert me["auth"] and me["setup_required"] and me["user"] is None
    assert c.get("/api/runs").status_code == 401                   # nothing without signing in
    assert c.get("/").status_code == 200                           # the app itself (sign-in page) loads
    bad = c.post("/api/auth/setup", json={"code": "nope", "username": "admin", "password": PW}, headers=W)
    assert bad.status_code == 403
    _setup_admin(c, appmod)
    assert c.get("/api/auth/me").json()["user"]["role"] == "admin"
    assert c.post("/api/auth/setup", json={"code": "x", "username": "b", "password": PW}, headers=W).status_code == 409

    for u, role in (("tess", "tester"), ("vic", "viewer")):
        r = c.post("/api/users", json={"username": u, "password": PW, "role": role}, headers=W)
        assert r.status_code == 200, r.text
    assert c.post("/api/users", json={"username": "x", "password": "short"}, headers=W).status_code == 400
    assert c.post("/api/users", json={"username": "tess"}, headers={}).status_code == 403          # no CSRF header

    tess, vic = _login(appmod, "tess"), _login(appmod, "vic")
    assert _client(appmod).post("/api/auth/login", json={"username": "tess", "password": "wrong-password"}).status_code == 401
    assert vic.get("/api/runs").status_code == 200
    assert vic.post("/api/runs", json={"text": "Open https://example.com"}, headers=W).status_code == 403
    assert tess.get("/api/users").status_code == 403
    assert tess.put("/api/ai", json={"provider": "anthropic"}, headers=W).status_code == 403
    assert tess.put("/api/grids/g", json={"name": "g", "url": "http://x/wd/hub"}, headers=W).status_code == 403

    # disabling a user signs them out at once; the last admin can't be demoted
    tid = next(u["id"] for u in c.get("/api/users").json() if u["username"] == "tess")
    assert c.put(f"/api/users/{tid}", json={"disabled": True}, headers=W).status_code == 200
    assert tess.get("/api/runs").status_code == 401
    aid = c.get("/api/auth/me").json()["user"]["id"]
    assert c.put(f"/api/users/{aid}", json={"role": "tester"}, headers=W).status_code == 400

    # passwords are scrypt hashes, never stored as given
    con = sqlite3.connect(appmod.db.path)
    assert all(h.startswith("scrypt$") and PW not in h for (h,) in con.execute("SELECT pw_hash FROM users"))
    assert c.post("/api/auth/logout", headers=W).status_code == 200 and c.get("/api/runs").status_code == 401


def _wait(c, rid, t=60):
    end = time.time() + t
    while time.time() < end:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] not in ("queued", "dispatched", "running"):
            return r
        time.sleep(0.3)
    raise AssertionError("run did not finish")


def test_owners_private_sessions_and_secrets(hub, demo_page):
    c, appmod = hub
    _setup_admin(c, appmod)
    for u in ("ann", "bob"):
        c.post("/api/users", json={"username": u, "password": PW, "role": "tester"}, headers=W)
    ann, bob = _login(appmod, "ann"), _login(appmod, "bob")
    steps = [{"action": "navigate", "value": demo_page.as_uri()}, {"action": "type", "target": "username field", "value": "${user.pw}"}]
    r = ann.post("/api/runs", json={"steps": steps, "name": "ann's", "data": {"user": {"pw": "s3cret-ann"}}, "sensitive": ["user.pw"],
                                    "config": {"use_llm": False, "record_video": False}}, headers=W)
    assert r.status_code == 200, r.text
    rid = r.json()["run_ids"][0]
    run = _wait(ann, rid)
    assert run["owner"] == "ann"
    assert [x["run_id"] for x in ann.get("/api/runs?mine=true").json()] == [rid]
    assert bob.get("/api/runs?mine=true").json() == [] and len(bob.get("/api/runs").json()) == 1   # team sees all
    assert bob.delete(f"/api/runs/{rid}", headers=W).status_code == 403
    # bob may replay ann's script, but not with ann's secret
    rr = bob.post("/api/runs", json={"replay_run_id": rid, "config": {"use_llm": False, "record_video": False}}, headers=W)
    assert rr.status_code == 200
    bob_rid = rr.json()["run_ids"][0]
    assert appmod.db.get_run(bob_rid)["owner"] == "bob"
    assert appmod.db.get_secrets(rid) == {"user.pw": "s3cret-ann"} and appmod.db.get_secrets(bob_rid) == {}
    assert ann.post("/api/runs", json={"replay_run_id": rid, "config": {"use_llm": False, "record_video": False}},
                    headers=W).status_code == 200
    assert appmod.db.get_secrets(ann.get("/api/runs?mine=true").json()[0]["run_id"]) == {"user.pw": "s3cret-ann"}   # owner: yes
    _wait(bob, bob_rid)
    # saved logins are private
    st = {"cookies": [{"name": "sid", "value": "ann-cookie", "domain": "example.com", "path": "/"}], "origins": []}
    up = ann.post("/api/sessions/ann-sso", files={"file": ("s.json", json.dumps(st), "application/json")}, headers=W)
    assert up.status_code == 200, up.text
    assert [s["name"] for s in ann.get("/api/sessions").json()] == ["ann-sso"]
    assert bob.get("/api/sessions").json() == []
    use = bob.post("/api/runs", json={"steps": steps[:1], "config": {"session": "ann-sso", "use_llm": False}}, headers=W)
    assert use.status_code == 403
    assert bob.delete("/api/sessions/ann-sso", headers=W).status_code == 403
    assert [s["name"] for s in c.get("/api/sessions").json()] == ["ann-sso"]      # admin sees all
    assert ann.delete(f"/api/runs/{rid}", headers=W).status_code == 200


def _fake_claude(tmp_path):
    """Stand-in Claude Code CLI: logs which token / home it got, answers with a fixed step list."""
    log = tmp_path / "claude-calls.jsonl"
    p = tmp_path / "fake_claude.py"
    steps = {"name": "t", "steps": [{"action": "click", "target": "Sign in button", "value": None, "nth": None,
                                     "optional": False, "raw": "sign in"}]}
    p.write_text("import json, os, sys\n"
                 f"open({str(log)!r}, 'a', encoding='utf-8').write(json.dumps({{'token': os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'),"
                 " 'home': os.environ.get('HOME'), 'claude_dir': os.environ.get('CLAUDE_CONFIG_DIR'),"
                 " 'api_key': os.environ.get('ANTHROPIC_API_KEY')}) + '\\n')\n"
                 f"print(json.dumps({{'type': 'result', 'is_error': False, 'structured_output': {steps!r}}}))\n", encoding="utf-8")
    return p, log


def test_each_user_uses_their_own_subscription(hub, tmp_path, monkeypatch):
    c, appmod = hub
    fake, log = _fake_claude(tmp_path)
    monkeypatch.delenv("AIAUTO_DISABLE_LLM", raising=False)
    monkeypatch.setenv("AIAUTO_CLAUDE_CLI", f'"{sys.executable}" "{fake}"' if sys.platform == "win32" else f"{sys.executable} {fake}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key-must-not-leak")
    for k in ("AIAUTO_CURSOR_CLI", "AIAUTO_COPILOT_CLI"):
        monkeypatch.setenv(k, str(tmp_path / "absent"))
    _setup_admin(c, appmod)
    for u in ("ann", "bob", "cat"):
        c.post("/api/users", json={"username": u, "password": PW, "role": "tester"}, headers=W)
    ann, bob, cat = (_login(appmod, u) for u in ("ann", "bob", "cat"))

    for who, tok in ((ann, "tok-ann"), (bob, "tok-bob")):
        r = who.put("/api/me/ai", json={"provider": "claude-code", "claude_token": tok, "model": "claude-sonnet-5-5"}, headers=W)
        assert r.status_code == 200 and r.json()["claude_token_set"] and "tok-" not in r.text      # write-only
    # the organisation's AI is off for people without their own
    assert c.put("/api/ai", json={"provider": "none", "users_may_use_org": False}, headers=W).status_code == 200

    for who in (ann, bob, cat):
        r = who.post("/api/parse", json={"text": "sign in", "use_llm": True}, headers=W)
        assert r.status_code == 200, r.text
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [x["token"] for x in calls] == ["tok-ann", "tok-bob"]                 # cat has no AI: no call at all
    assert calls[0]["home"] != calls[1]["home"] and "ai-home" in calls[0]["home"]   # private CLI profile each
    assert all(x["api_key"] is None for x in calls)                              # the server's key never leaks in
    assert cat.post("/api/parse", json={"text": "click Login", "use_llm": True}, headers=W).json()["parser"] == "rules"
    assert ann.post("/api/parse", json={"text": "sign in", "use_llm": True}, headers=W).json()["parser"].startswith("llm:claude-code")
    # tokens are encrypted at rest
    raw = b"".join(r[0].encode() for r in sqlite3.connect(appmod.db.path).execute("SELECT data_enc FROM user_ai"))
    assert b"tok-ann" not in raw and b"tok-bob" not in raw
    # a user without a token cannot borrow the server's own Claude login
    st = cat.put("/api/me/ai", json={"provider": "claude-code"}, headers=W).json()
    assert "add your own token" in json.dumps(st["providers"])


# ----------------------------------------------------------------------------------------------- SSO
class _FakeIdP:
    """A minimal OpenID Connect provider: discovery, authorize (auto-approves), token, JWKS. Signs RS256."""

    def __init__(self, email="dana@corp.example", sub="idp-123"):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.email, self.sub, self.codes = email, sub, {}
        idp = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/.well-known/openid-configuration":
                    return self._json({"issuer": idp.url, "authorization_endpoint": idp.url + "/authorize",
                                       "token_endpoint": idp.url + "/token", "jwks_uri": idp.url + "/jwks"})
                if u.path == "/jwks":
                    import jwt
                    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(idp.key.public_key()))
                    return self._json({"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]})
                if u.path == "/authorize":
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    code = base64.urlsafe_b64encode(q["state"].encode()).decode()
                    idp.codes[code] = q
                    self.send_response(302)
                    self.send_header("Location", q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]}))
                    self.end_headers()

            def do_POST(self):
                import jwt
                body = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                q = idp.codes.pop(body["code"][0])
                now = int(time.time())
                tok = jwt.encode({"iss": idp.url, "aud": q["client_id"], "sub": idp.sub, "email": idp.email, "email_verified": True,
                                  "name": "Dana Smith", "nonce": q["nonce"], "iat": now, "exp": now + 300},
                                 idp.key, algorithm="RS256", headers={"kid": "k1"})
                self._json({"id_token": tok, "access_token": "at", "token_type": "Bearer"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def _sso_login(appmod, idp):
    c = _client(appmod)
    r = c.get("/api/auth/sso/start", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(idp.url + "/authorize")
    back = httpx.get(r.headers["location"], follow_redirects=False).headers["location"]
    done = c.get(urlparse(back).path + "?" + urlparse(back).query, follow_redirects=False)
    return c, done


def test_sso_sign_in_creates_and_links_accounts(hub):
    c, appmod = hub
    _setup_admin(c, appmod)
    idp = _FakeIdP()
    r = c.put("/api/auth/sso-config", json={"enabled": True, "issuer": idp.url, "client_id": "autopilot", "client_secret": "cs-1",
                                            "domains": ["corp.example"], "default_role": "tester", "label": "Sign in with Corp"}, headers=W)
    assert r.status_code == 200, r.text
    assert r.json()["client_secret_set"] and "cs-1" not in r.text
    assert _client(appmod).get("/api/auth/me").json()["sso"] == {"enabled": True, "label": "Sign in with Corp"}

    dana, done = _sso_login(appmod, idp)
    assert done.status_code == 303 and done.headers["location"] == "/#/"
    me = dana.get("/api/auth/me").json()["user"]
    assert me["email"] == "dana@corp.example" and me["role"] == "tester" and me["sso"] and me["name"] == "Dana Smith"
    again, _ = _sso_login(appmod, idp)                               # same person next time: same account
    assert again.get("/api/auth/me").json()["user"]["id"] == me["id"]

    outsider = _FakeIdP(email="eve@elsewhere.example", sub="idp-999")
    c.put("/api/auth/sso-config", json={"issuer": outsider.url}, headers=W)
    _, denied = _sso_login(appmod, outsider)
    assert denied.status_code == 303 and "domain" in denied.headers["location"]

    # a forged callback (no matching state cookie) is refused
    forged = _client(appmod).get("/api/auth/sso/callback?code=x&state=y", follow_redirects=False)
    assert forged.status_code == 303 and "error" in forged.headers["location"]


def test_projects_keep_teams_apart(hub, demo_page):
    """Team A's runs, reports, files, scripts, saved logins and live events are invisible to team B."""
    c, appmod = hub
    _setup_admin(c, appmod)
    for u in ("ann", "bob"):
        c.post("/api/users", json={"username": u, "password": PW, "role": "tester"}, headers=W)
    assert c.put("/api/projects/payments", json={"name": "Payments", "members": ["ann"]}, headers=W).status_code == 200
    assert c.put("/api/projects/search", json={"name": "Search", "members": ["bob"]}, headers=W).status_code == 200
    ann, bob = _login(appmod, "ann"), _login(appmod, "bob")
    assert {p["key"] for p in ann.get("/api/projects").json()} == {"default", "payments"}
    assert "members" not in ann.get("/api/projects").json()[0]                        # only admins see members
    assert bob.put("/api/projects/x", json={"name": "X"}, headers=W).status_code == 403  # admins make projects
    PA = {**W, "X-Project": "payments"}
    assert bob.get("/api/runs", headers={"X-Project": "payments"}).status_code == 403

    steps = [{"action": "navigate", "value": demo_page.as_uri()}]
    r = ann.post("/api/runs", headers=PA, json={"steps": steps, "name": "pay", "config": {"use_llm": False, "record_video": False}})
    assert r.status_code == 200, r.text
    rid = r.json()["run_ids"][0]
    _wait(ann, rid)
    run = c.get(f"/api/runs/{rid}").json()
    assert run["project"] == "payments" and run["owner"] == "ann"
    assert [x["run_id"] for x in ann.get("/api/runs", headers={"X-Project": "payments"}).json()] == [rid]
    assert ann.get("/api/runs").json() == []                                         # default project: empty
    for path in (f"/api/runs/{rid}", f"/api/runs/{rid}/files", f"/api/runs/{rid}/script", f"/artifacts/{rid}/summary.json",
                 f"/api/runs/{rid}/export", f"/api/runs/{rid}/download"):
        assert bob.get(path).status_code == 404, path
        assert ann.get(path).status_code in (200, 409), path
    assert bob.post("/api/runs/cancel", json={"run_ids": [rid]}, headers=W).json()["cancelled"][rid] == "not found"
    assert bob.post("/api/runs", headers=W, json={"replay_run_id": rid}).status_code == 404
    lib = ann.post("/api/library/from-run", json={"run_id": rid, "name": "pay-flow"}, headers=PA)
    assert lib.status_code == 200 and lib.json()["project"] == "payments"
    assert bob.get("/api/library/pay-flow").status_code == 404
    assert [x["name"] for x in ann.get("/api/library", headers={"X-Project": "payments"}).json()] == ["pay-flow"]
    assert ann.get("/api/stats", headers={"X-Project": "payments"}).json()["total"] == 1
    assert c.get("/api/stats", headers={"X-Project": "payments"}).json()["total"] == 1   # admin sees every project
    assert c.get("/api/stats", headers={"X-Project": "search"}).json()["total"] == 0
    # deleting a project moves its work to Default instead of losing it
    assert c.delete("/api/projects/payments", headers=W).json()["moved_to"] == "default"
    assert c.get(f"/api/runs/{rid}").json()["project"] == "default"
