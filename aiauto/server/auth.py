"""Accounts for a shared hub: built-in users (username + password) and company SSO (OpenID Connect), roles,
browser sessions, and each user's own AI settings (their subscription token or key, encrypted at rest).

    AIAUTO_AUTH=off                  single-user mode (no login), e.g. a hub only you use on your laptop
    AIAUTO_ADMIN_USER / _PASSWORD    create the first admin at startup (Docker); otherwise the first visitor
                                     needs the one-time setup code printed in the server log
    AIAUTO_SECRET_KEY                key for encrypting secrets at rest (default: generated next to the DB)
    AIAUTO_PUBLIC_URL                external URL (https://autopilot.example.com) for SSO redirects

Roles: admin (everything, users, SSO, organisation AI, machines, grids) · tester (create, run, replay, library,
own saved logins) · viewer (read runs, reports, videos).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

log = logging.getLogger("aiauto.auth")

ROLES = ("admin", "tester", "viewer")
COOKIE = "aiauto_session"
SSO_COOKIE = "aiauto_sso"
SESSION_SECONDS = int(os.getenv("AIAUTO_SESSION_HOURS", "168")) * 3600
CSRF_HEADER = "x-requested-with"


def enabled() -> bool:
    return os.getenv("AIAUTO_AUTH", "on").lower() not in ("off", "0", "false", "no")


# ------------------------------------------------------------------------------------------ secrets at rest
class Vault:
    """Fernet encryption for secrets stored in the DB (users' AI tokens / keys, SSO client secret)."""

    def __init__(self, db_path: str):
        from cryptography.fernet import Fernet
        raw = os.getenv("AIAUTO_SECRET_KEY")
        if raw:
            key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        else:
            p = Path(db_path).with_suffix(".key")
            if not p.exists():
                p.write_bytes(Fernet.generate_key())
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
            key = p.read_bytes().strip()
        self._f = Fernet(key)

    def enc(self, data: Any) -> str:
        return self._f.encrypt(json.dumps(data).encode()).decode()

    def dec(self, token: str | None, default: Any = None) -> Any:
        if not token:
            return default
        try:
            return json.loads(self._f.decrypt(token.encode()))
        except Exception:
            log.warning("could not decrypt a stored secret (was AIAUTO_SECRET_KEY changed?)")
            return default


# ------------------------------------------------------------------------------------------ passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, h = (stored or "").split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def check_password_rules(pw: str) -> None:
    if len(pw or "") < 10:
        raise HTTPException(400, "Password must be at least 10 characters")


USERNAME = re.compile(r"^[A-Za-z0-9._@-]{2,64}$")


def public_user(u: dict | None) -> dict | None:
    if not u:
        return None
    return {"id": u["id"], "username": u["username"], "name": u.get("name") or u["username"], "email": u.get("email"),
            "role": u["role"], "sso": bool(u.get("sso_issuer")), "disabled": bool(u.get("disabled")),
            "has_password": bool(u.get("pw_hash")) if "pw_hash" in u else bool(u.get("has_password")),
            "created": u.get("created"), "last_login": u.get("last_login")}


LOCAL_USER = {"id": 0, "username": "local", "name": "Local user", "email": None, "role": "admin", "pw_hash": None}


class Auth:
    def __init__(self, db, data_dir: Path):
        self.db = db
        self.vault = Vault(db.path)
        self.data_dir = Path(data_dir)
        self.setup_code: str | None = None
        self._fails: dict[str, list[float]] = {}
        self._oidc_cache: dict[str, tuple[float, dict]] = {}

    # -------------------------------------------------------------- startup
    def bootstrap(self) -> None:
        if not enabled():
            return
        if self.db.user_count() == 0:
            u, pw = os.getenv("AIAUTO_ADMIN_USER"), os.getenv("AIAUTO_ADMIN_PASSWORD")
            if u and pw:
                check_password_rules(pw)
                self.db.user_create(u, "admin", hash_password(pw), name=u)
                log.info("created admin user %r from AIAUTO_ADMIN_USER", u)
            else:
                self.setup_code = "-".join(secrets.token_hex(3) for _ in range(3))
                log.warning("No users yet. Open the web app and create the admin account with setup code: %s", self.setup_code)

    # -------------------------------------------------------------- sessions
    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def login_response(self, user: dict, request: Request, body: dict | None = None, redirect: str | None = None):
        token = secrets.token_urlsafe(32)
        self.db.auth_session_put(self._h(token), user["id"], time.time() + SESSION_SECONDS)
        self.db.user_update(user["id"], last_login=time.time())
        resp = RedirectResponse(redirect, status_code=303) if redirect else JSONResponse(body or {"user": public_user(user)})
        resp.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax", secure=is_https(request), path="/")
        return resp

    def user_from_request(self, request_or_ws) -> dict | None:
        if not enabled():
            return LOCAL_USER
        token = request_or_ws.cookies.get(COOKIE)
        return self.db.auth_session_user(self._h(token)) if token else None

    def logout(self, request: Request) -> None:
        token = request.cookies.get(COOKIE)
        if token:
            self.db.auth_session_delete(self._h(token))

    # -------------------------------------------------------------- brute force guard
    def _guard(self, key: str) -> None:
        now = time.time()
        recent = [t for t in self._fails.get(key, []) if now - t < 600]
        self._fails[key] = recent
        if len(recent) >= 8:
            raise HTTPException(429, "Too many failed sign-ins. Try again in a few minutes.")

    def _failed(self, key: str) -> None:
        self._fails.setdefault(key, []).append(time.time())

    # -------------------------------------------------------------- per-user AI
    def ai_get(self, user: dict) -> dict:
        if not user or not user.get("id"):
            return {}
        return self.vault.dec(self.db.user_ai_get(user["id"]), {}) or {}

    def ai_put(self, user: dict, cfg: dict) -> None:
        self.db.user_ai_put(user["id"], self.vault.enc(cfg))

    def ai_for(self, user: dict | None, org_cfg: dict) -> dict | None:
        """Settings the AI uses for this user's work. None = the organisation's (hub) settings."""
        if not enabled() or not user or not user.get("id"):
            return None
        mine = self.ai_get(user)
        if mine.get("provider") and mine["provider"] != "org":
            return {**mine, "scoped": True, "isolate_dir": str(self.data_dir / "ai-home" / str(user["id"]))}
        if org_cfg.get("users_may_use_org", True):
            return None
        return {"provider": "none"}

    # -------------------------------------------------------------- SSO (OpenID Connect)
    def sso_cfg(self) -> dict:
        c = dict(self.db.setting_get("sso", {}) or {})
        c["client_secret"] = self.vault.dec(c.pop("client_secret_enc", None))
        return c

    def sso_enabled(self) -> bool:
        c = self.db.setting_get("sso", {}) or {}
        return bool(c.get("enabled") and c.get("issuer") and c.get("client_id"))

    def oidc(self, issuer: str) -> dict:
        hit = self._oidc_cache.get(issuer)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        r = httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=15)
        r.raise_for_status()
        meta = r.json()
        if meta.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
            raise HTTPException(502, "SSO provider issuer mismatch")
        self._oidc_cache[issuer] = (time.time(), meta)
        return meta


def is_https(request) -> bool:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.split(",")[0].strip() == "https"


def base_url(request) -> str:
    if os.getenv("AIAUTO_PUBLIC_URL"):
        return os.environ["AIAUTO_PUBLIC_URL"].rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{'https' if is_https(request) else request.url.scheme}://{host}"


# ------------------------------------------------------------------------------------------ access rules
# paths anyone may call (the sign-in page, health, and agent endpoints that use the agent token)
PUBLIC = ("/api/health", "/api/auth/me", "/api/auth/login", "/api/auth/setup", "/api/auth/logout",
          "/api/auth/sso/start", "/api/auth/sso/callback", "/api/agents/upload/", "/api/ai/call", "/assets/")
ADMIN_ONLY = [("PUT", "/api/ai"), ("POST", "/api/ai/test"), ("PUT", "/api/grids/"), ("DELETE", "/api/grids/"),
              ("DELETE", "/api/agents/"), ("*", "/api/users"), ("*", "/api/auth/sso-config"), ("*", "/api/agent-tokens"),
              ("PUT", "/api/projects/"), ("DELETE", "/api/projects/"),
              ("PUT", "/api/connections/"), ("DELETE", "/api/connections/"), ("POST", "/api/connections/")]
VIEWER_MAY_WRITE = ("/api/auth/logout", "/api/me/")


def is_public(path: str) -> bool:
    if not (path.startswith("/api/") or path.startswith("/artifacts/")):
        return True            # the web app itself (it shows the sign-in page)
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in PUBLIC)


def denied(user: dict, method: str, path: str) -> str | None:
    role = user.get("role")
    if role == "admin":
        return None
    for m, prefix in ADMIN_ONLY:
        if (m == "*" or m == method) and (path == prefix or path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/")):
            return "Only an admin can do this"
    if role == "viewer" and method not in ("GET", "HEAD", "OPTIONS") and not path.startswith(VIEWER_MAY_WRITE):
        return "Viewers can look at runs and reports, not change them"
    if role not in ROLES:
        return "Unknown role"
    return None


def can_manage(user: dict, owner: str | None) -> bool:
    """Owner of the thing, or an admin. Things from before accounts existed (owner None) are open to testers."""
    return user.get("role") == "admin" or owner is None or owner == user.get("username")


# ------------------------------------------------------------------------------------------ API
class LoginReq(BaseModel):
    username: str
    password: str


class SetupReq(BaseModel):
    code: Optional[str] = None
    username: str
    password: str
    name: Optional[str] = None
    email: Optional[str] = None


class PasswordReq(BaseModel):
    current: Optional[str] = None
    new: str


class UserReq(BaseModel):
    username: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None
    disabled: Optional[bool] = None


class MyAIReq(BaseModel):
    provider: Optional[str] = None          # org | claude-code | cursor | copilot | anthropic | litellm | none
    model: Optional[str] = None
    claude_token: Optional[str] = None      # write-only; "" clears
    copilot_token: Optional[str] = None
    cursor_key: Optional[str] = None
    anthropic_key: Optional[str] = None
    litellm_model: Optional[str] = None
    litellm_base: Optional[str] = None
    litellm_key: Optional[str] = None


class SSOReq(BaseModel):
    enabled: Optional[bool] = None
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None     # write-only
    domains: Optional[list[str]] = None     # allowed e-mail domains (empty = any)
    default_role: Optional[str] = None
    auto_create: Optional[bool] = None
    label: Optional[str] = None


MY_AI_SECRETS = ("claude_token", "copilot_token", "cursor_key", "anthropic_key", "litellm_key")


def make_router(auth: Auth, org_ai, providers_mod) -> APIRouter:
    r = APIRouter()

    def me(request: Request) -> dict:
        u = auth.user_from_request(request)
        if not u:
            raise HTTPException(401, "Sign in first")
        return u

    @r.get("/api/auth/me")
    def auth_me(request: Request):
        u = auth.user_from_request(request)
        sso = auth.db.setting_get("sso", {}) or {}
        return {"auth": enabled(), "user": public_user(u), "setup_required": enabled() and auth.db.user_count() == 0,
                "sso": {"enabled": auth.sso_enabled(), "label": sso.get("label") or "Sign in with SSO"}}

    @r.post("/api/auth/setup")
    def auth_setup(req: SetupReq, request: Request):
        if not enabled() or auth.db.user_count() > 0:
            raise HTTPException(409, "Setup is already done")
        auth._guard("setup")
        if not auth.setup_code or not hmac.compare_digest((req.code or "").strip(), auth.setup_code):
            auth._failed("setup")
            raise HTTPException(403, "Wrong setup code (it is printed in the server log)")
        if not USERNAME.match(req.username):
            raise HTTPException(400, "Username: 2-64 letters, digits, . _ @ -")
        check_password_rules(req.password)
        u = auth.db.user_create(req.username, "admin", hash_password(req.password), name=req.name or req.username, email=req.email)
        auth.setup_code = None
        log.info("first admin %r created", req.username)
        return auth.login_response(u, request)

    @r.post("/api/auth/login")
    def auth_login(req: LoginReq, request: Request):
        key = f"{req.username.lower()}|{request.client.host if request.client else ''}"
        auth._guard(key)
        u = auth.db.user_get(username=req.username)
        if not u or u["disabled"] or not verify_password(req.password, u["pw_hash"]):
            auth._failed(key)
            time.sleep(0.3)
            raise HTTPException(401, "Wrong username or password")
        return auth.login_response(u, request)

    @r.post("/api/auth/logout")
    def auth_logout(request: Request):
        auth.logout(request)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @r.post("/api/me/password")
    def my_password(req: PasswordReq, request: Request):
        u = me(request)
        if u.get("pw_hash") and not verify_password(req.current or "", u["pw_hash"]):
            raise HTTPException(403, "Current password is wrong")
        check_password_rules(req.new)
        auth.db.user_update(u["id"], pw_hash=hash_password(req.new))
        auth.db.auth_session_delete(user_id=u["id"])            # sign out everywhere else
        return auth.login_response(auth.db.user_get(u["id"]), request, {"ok": True})

    # ---- my own AI (my subscription / key)
    def my_ai_public(u: dict) -> dict:
        cfg = auth.ai_get(u)
        out = {k: cfg.get(k) for k in ("provider", "model", "litellm_model", "litellm_base")}
        out.update({f"{k}_set": bool(cfg.get(k)) for k in MY_AI_SECRETS})
        out["provider"] = out["provider"] or "org"
        org = org_ai()
        out["org_allowed"] = org.get("users_may_use_org", True)
        with providers_mod.using(auth.ai_for(u, org)):
            out["status"] = providers_mod.status()
            out["providers"] = providers_mod.report()
        return out

    @r.get("/api/me/ai")
    def my_ai(request: Request):
        return my_ai_public(me(request))

    @r.put("/api/me/ai")
    def my_ai_put(req: MyAIReq, request: Request):
        u = me(request)
        if not u.get("id"):
            raise HTTPException(400, "Personal AI settings need accounts (AIAUTO_AUTH is off: use Settings → AI)")
        cfg = auth.ai_get(u)
        for k, v in req.model_dump().items():
            if v is None:
                continue
            if v == "":
                cfg.pop(k, None)
            else:
                cfg[k] = v
        if cfg.get("provider") not in (None, "org", "none", "auto", *[p for p in providers_mod.ORDER if p != "hub"]):
            raise HTTPException(400, "unknown provider")
        auth.ai_put(u, cfg)
        return my_ai_public(u)

    @r.post("/api/me/ai/test")
    def my_ai_test(request: Request):
        u = me(request)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        t0 = time.time()
        with providers_mod.using(auth.ai_for(u, org_ai())):
            out = providers_mod.call_json("You check that an AI connection works.", "Reply ok=true.", schema, None, 2000, "low")
            prov, st = providers_mod.active(), providers_mod.status()
        return {"ok": bool(out and out.get("ok")), "seconds": round(time.time() - t0, 1), "provider": prov, "status": st}

    # ---- users (admin)
    @r.get("/api/users")
    def users():
        return [public_user(u) for u in auth.db.users()]

    @r.post("/api/users")
    def user_create(req: UserReq):
        if not req.username or not USERNAME.match(req.username):
            raise HTTPException(400, "Username: 2-64 letters, digits, . _ @ -")
        if auth.db.user_get(username=req.username):
            raise HTTPException(409, "That username exists")
        role = req.role or "tester"
        if role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        if req.password:
            check_password_rules(req.password)
        u = auth.db.user_create(req.username, role, hash_password(req.password) if req.password else None,
                                name=req.name or req.username, email=req.email)
        return public_user(u)

    @r.put("/api/users/{uid}")
    def user_update(uid: int, req: UserReq, request: Request):
        u = auth.db.user_get(uid)
        if not u:
            raise HTTPException(404)
        f: dict = {}
        if req.role is not None:
            if req.role not in ROLES:
                raise HTTPException(400, f"role must be one of {ROLES}")
            f["role"] = req.role
        if req.disabled is not None:
            f["disabled"] = 1 if req.disabled else 0
        for k in ("name", "email"):
            if getattr(req, k) is not None:
                f[k] = getattr(req, k)
        if req.password:
            check_password_rules(req.password)
            f["pw_hash"] = hash_password(req.password)
        me_ = me(request)
        if uid == me_["id"] and (f.get("role", "admin") != "admin" or f.get("disabled")):
            raise HTTPException(400, "You can't demote or disable yourself")
        if (f.get("role") not in (None, "admin") or f.get("disabled")) and u["role"] == "admin" and \
                sum(1 for x in auth.db.users() if x["role"] == "admin" and not x["disabled"]) <= 1:
            raise HTTPException(400, "Keep at least one active admin")
        auth.db.user_update(uid, **f)
        if f.get("disabled") or "pw_hash" in f or "role" in f:
            auth.db.auth_session_delete(user_id=uid)         # take effect now
        return public_user(auth.db.user_get(uid))

    @r.delete("/api/users/{uid}")
    def user_delete(uid: int, request: Request):
        if uid == me(request)["id"]:
            raise HTTPException(400, "You can't delete yourself")
        auth.db.user_delete(uid)
        return {"deleted": uid}

    # ---- SSO
    @r.get("/api/auth/sso-config")
    def sso_get(request: Request):
        c = auth.db.setting_get("sso", {}) or {}
        return {**{k: v for k, v in c.items() if k != "client_secret_enc"}, "client_secret_set": bool(c.get("client_secret_enc")),
                "redirect_uri": base_url(request) + "/api/auth/sso/callback"}

    @r.put("/api/auth/sso-config")
    def sso_put(req: SSOReq, request: Request):
        c = dict(auth.db.setting_get("sso", {}) or {})
        for k, v in req.model_dump().items():
            if v is None:
                continue
            if k == "client_secret":
                if v:
                    c["client_secret_enc"] = auth.vault.enc(v)
                else:
                    c.pop("client_secret_enc", None)
            else:
                c[k] = v
        if c.get("default_role", "tester") not in ROLES:
            raise HTTPException(400, f"default_role must be one of {ROLES}")
        if c.get("enabled") and c.get("issuer"):
            try:
                auth._oidc_cache.pop(c["issuer"], None)
                auth.oidc(c["issuer"])
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(400, f"Could not read the provider's OpenID configuration: {e}")
        auth.db.setting_put("sso", c)
        return sso_get(request)

    @r.get("/api/auth/sso/start")
    def sso_start(request: Request):
        if not auth.sso_enabled():
            raise HTTPException(404, "SSO is not configured")
        c = auth.sso_cfg()
        meta = auth.oidc(c["issuer"])
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": base_url(request) + "/api/auth/sso/callback",
                  "scope": "openid email profile", "state": state, "nonce": nonce,
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        resp = RedirectResponse(meta["authorization_endpoint"] + "?" + urlencode(params), status_code=302)
        resp.set_cookie(SSO_COOKIE, auth.vault.enc({"state": state, "nonce": nonce, "verifier": verifier, "t": time.time()}),
                        max_age=600, httponly=True, samesite="lax", secure=is_https(request), path="/api/auth/sso")
        return resp

    @r.get("/api/auth/sso/callback")
    def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        import jwt
        def fail(msg: str):
            log.warning("SSO sign-in failed: %s", msg)
            return RedirectResponse("/#/login?error=" + quote(msg), status_code=303)
        if error:
            return fail(f"provider said: {error}")
        pending = auth.vault.dec(request.cookies.get(SSO_COOKIE), {}) or {}
        if not pending or not hmac.compare_digest(pending.get("state", ""), state) or time.time() - pending.get("t", 0) > 600:
            return fail("sign-in expired, try again")
        c = auth.sso_cfg()
        meta = auth.oidc(c["issuer"])
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": base_url(request) + "/api/auth/sso/callback",
                "client_id": c["client_id"], "code_verifier": pending["verifier"]}
        if c.get("client_secret"):
            data["client_secret"] = c["client_secret"]
        try:
            tr = httpx.post(meta["token_endpoint"], data=data, timeout=20, headers={"Accept": "application/json"})
            tr.raise_for_status()
            id_token = tr.json()["id_token"]
            jwks = jwt.PyJWKClient(meta["jwks_uri"])
            key = jwks.get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(id_token, key, algorithms=["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"],
                                audience=c["client_id"], issuer=meta["issuer"], leeway=60)
        except Exception as e:
            return fail(f"could not verify the sign-in ({type(e).__name__})")
        if not hmac.compare_digest(claims.get("nonce", ""), pending["nonce"]):
            return fail("sign-in did not match (nonce)")
        email = (claims.get("email") or "").lower()
        if claims.get("email_verified") is False:
            return fail("your e-mail address is not verified at the provider")
        domains = [d.lower().lstrip("@") for d in (c.get("domains") or []) if d]
        if domains and email.split("@")[-1] not in domains:
            return fail("your e-mail domain is not allowed here")
        u = auth.db.user_by_sso(meta["issuer"], claims["sub"])
        if not u and email:
            u = auth.db.user_by_email(email)
            if u and not u.get("sso_sub"):
                auth.db.user_update(u["id"], sso_issuer=meta["issuer"], sso_sub=claims["sub"])
            elif u:
                u = None
        if not u:
            if not c.get("auto_create", True):
                return fail("no account for you yet: ask an admin")
            base = re.sub(r"[^A-Za-z0-9._@-]", "", (claims.get("preferred_username") or email or claims["sub"]))[:60] or "user"
            name = base
            while auth.db.user_get(username=name):
                name = f"{base}-{secrets.token_hex(2)}"
            u = auth.db.user_create(name, c.get("default_role") or "tester", None, name=claims.get("name") or name,
                                    email=email or None, sso_issuer=meta["issuer"], sso_sub=claims["sub"])
        if u["disabled"]:
            return fail("your account is disabled")
        resp = auth.login_response(u, request, redirect="/#/")
        resp.delete_cookie(SSO_COOKIE, path="/api/auth/sso")
        return resp

    return r
