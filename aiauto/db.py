"""Local SQLite store for the hub: runs, steps, script library, saved sessions and registered machines.

Everything the web app shows comes from here, so restarting the server never loses history.
Large binary artifacts (videos, screenshots, reports) stay on disk under the artifacts folder;
the database keeps the metadata and paths.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, test_name TEXT, status TEXT, engine TEXT, browser TEXT, agent TEXT, mode TEXT,
  started_at TEXT, finished_at TEXT, duration_ms INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0, passed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, healed INTEGER DEFAULT 0,
  current_step TEXT, video TEXT, thumb TEXT, error TEXT, iteration TEXT, batch_id TEXT,
  config_json TEXT, created REAL, data_json TEXT, sensitive_json TEXT, source_run TEXT
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created DESC);
CREATE TABLE IF NOT EXISTS steps (
  run_id TEXT, idx INTEGER, status TEXT, raw TEXT, action TEXT, target TEXT, value TEXT, locator TEXT,
  error TEXT, screenshot TEXT, url TEXT, duration_ms INTEGER, healed INTEGER DEFAULT 0, dialog TEXT,
  PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS run_secrets (run_id TEXT PRIMARY KEY, secrets_json TEXT);
CREATE TABLE IF NOT EXISTS library (
  name TEXT PRIMARY KEY, test_name TEXT, steps INTEGER, engine TEXT, sensitive TEXT, test_json TEXT,
  source_run TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS sessions (name TEXT PRIMARY KEY, state_json TEXT, domains TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, name TEXT, email TEXT, role TEXT NOT NULL,
  pw_hash TEXT, sso_issuer TEXT, sso_sub TEXT, disabled INTEGER DEFAULT 0, created REAL, last_login REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_sso ON users(sso_issuer, sso_sub) WHERE sso_sub IS NOT NULL;
CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER, created REAL, expires REAL);
CREATE TABLE IF NOT EXISTS user_ai (user_id INTEGER PRIMARY KEY, data_enc TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS projects (key TEXT PRIMARY KEY, name TEXT, description TEXT, created REAL);
CREATE TABLE IF NOT EXISTS project_members (project TEXT, user_id INTEGER, PRIMARY KEY (project, user_id));
CREATE TABLE IF NOT EXISTS agent_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, token_hash TEXT UNIQUE, created REAL, created_by TEXT,
  last_used REAL, last_machine TEXT, revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS machines (
  name TEXT PRIMARY KEY, hostname TEXT, platform TEXT, engines TEXT, browsers TEXT, version TEXT,
  first_seen REAL, last_seen REAL, online INTEGER DEFAULT 0
);
"""

LIVE = ("queued", "dispatched", "running")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(runs)")}
        for col in ("data_json", "sensitive_json", "source_run", "parser", "owner"):
            if col not in have:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {col} TEXT")
        for table in ("library", "sessions"):
            if "owner" not in {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN owner TEXT")
        for table in ("runs", "library", "sessions"):     # everything belongs to a project ("default" before projects)
            if "project" not in {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN project TEXT DEFAULT 'default'")
        self._conn.execute("INSERT OR IGNORE INTO projects (key, name, description, created) VALUES "
                           "('default', 'Default', 'Open to everyone', ?)", (time.time(),))
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(steps)")}
        for col in ("parsed_by", "picked_by", "origin"):
            if col not in have:
                self._conn.execute(f"ALTER TABLE steps ADD COLUMN {col} TEXT")
        try:
            os.chmod(self.path, 0o600)   # holds replay secrets: owner-only
        except OSError:
            pass

    @contextmanager
    def tx(self):
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: Iterable = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable = ()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: Iterable = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(args))

    # ------------------------------------------------------------------ startup
    def recover(self) -> int:
        """Runs that were in flight when the server stopped can't finish any more: mark them."""
        rows = self.q(f"SELECT run_id FROM runs WHERE status IN ({','.join('?' * len(LIVE))})", LIVE)
        self.x(f"UPDATE runs SET status='interrupted', error=COALESCE(error,'Server restarted while this run was in progress'), "
               f"finished_at=? WHERE status IN ({','.join('?' * len(LIVE))})", (now_iso(), *LIVE))
        self.x("UPDATE machines SET online=0")
        return len(rows)

    # ------------------------------------------------------------------ runs
    def create_run(self, run: dict) -> None:
        cols = ["run_id", "test_name", "status", "engine", "browser", "agent", "mode", "started_at", "total",
                "iteration", "batch_id", "config_json", "created", "data_json", "sensitive_json", "source_run", "parser", "owner",
                "project"]
        run = {**run, "created": run.get("created", time.time()), "project": run.get("project") or "default"}
        self.x(f"INSERT OR REPLACE INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
               [run.get(c) for c in cols])

    def update_run(self, run_id: str, **fields) -> None:
        if not fields:
            return
        self.x(f"UPDATE runs SET {', '.join(f'{k}=?' for k in fields)} WHERE run_id=?", (*fields.values(), run_id))

    def on_event(self, ev: dict) -> None:
        """Apply a runner event (from a local run or an agent) to the database."""
        rid, t = ev.get("run_id"), ev.get("type")
        if not rid:
            return
        if t == "run_started":
            self.update_run(rid, status="running", total=ev.get("total", 0), started_at=now_iso(),
                            **({"agent": ev["agent"]} if ev.get("agent") else {}))
        elif t == "step_started":
            self.update_run(rid, current_step=ev.get("raw"))
            self.x("INSERT OR REPLACE INTO steps (run_id, idx, status, raw, action, target, value, origin, parsed_by) VALUES (?,?,?,?,?,?,?,?,?)",
                   (rid, ev["index"], "running", ev.get("raw"), ev.get("action"), ev.get("target"), ev.get("value"), ev.get("origin"), ev.get("parsed_by")))
        elif t == "step_finished":
            with self.tx() as c:
                c.execute("""INSERT INTO steps (run_id, idx, status, raw, action, locator, error, screenshot, url, duration_ms, healed, dialog, picked_by)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(run_id, idx) DO UPDATE SET status=excluded.status, locator=excluded.locator, picked_by=excluded.picked_by,
                             error=excluded.error, screenshot=excluded.screenshot, url=excluded.url,
                             duration_ms=excluded.duration_ms, healed=excluded.healed, dialog=excluded.dialog,
                             raw=COALESCE(excluded.raw, steps.raw), action=COALESCE(excluded.action, steps.action)""",
                          (rid, ev["index"], ev.get("status"), ev.get("raw"), ev.get("action"), ev.get("locator"), ev.get("error"),
                           ev.get("screenshot"), ev.get("url"), ev.get("duration_ms"), 1 if ev.get("healed") else 0, ev.get("dialog"),
                           ev.get("picked_by")))
                st = ev.get("status")
                c.execute(f"""UPDATE runs SET done=done+1, passed=passed+?, failed=failed+?, healed=healed+?,
                              thumb=COALESCE(?, thumb) WHERE run_id=?""",
                          (1 if st == "passed" else 0, 1 if st == "failed" else 0, 1 if ev.get("healed") else 0,
                           ev.get("screenshot"), rid))
        elif t == "run_finished":
            self.update_run(rid, status=ev.get("status", "failed"), finished_at=now_iso(), current_step=None,
                            **({"error": ev["error"]} if ev.get("error") else {}))

    def finalize_from_summary(self, run_dir: Path) -> None:
        """Make the DB match summary.json (authoritative once a run's artifacts exist)."""
        p = Path(run_dir) / "summary.json"
        if not p.exists():
            return
        s = json.loads(p.read_text(encoding="utf-8"))
        res = s.get("results", [])
        shots = [r["screenshot"] for r in res if r.get("screenshot")]
        err = next((r.get("error") for r in res if r.get("status") == "failed"), None)
        existing = self.one("SELECT run_id, created, config_json, batch_id, iteration, browser, data_json, sensitive_json, source_run, parser, owner, project FROM runs WHERE run_id=?", (s["run_id"],))
        created = existing["created"] if existing else (datetime.fromisoformat(s["started_at"]).timestamp() if s.get("started_at") else p.stat().st_mtime)
        with self.tx() as c:
            c.execute("""INSERT OR REPLACE INTO runs (run_id, test_name, status, engine, browser, agent, mode, started_at, finished_at,
                         duration_ms, total, done, passed, failed, healed, current_step, video, thumb, error, iteration, batch_id, config_json, created,
                         data_json, sensitive_json, source_run, parser, owner, project)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (s["run_id"], s.get("test_name"), s.get("status"), s.get("engine"), existing and existing["browser"], s.get("agent"),
                       s.get("mode"), s.get("started_at"), s.get("finished_at"), s.get("duration_ms", 0), len(res), len(res),
                       sum(1 for r in res if r.get("status") == "passed"), sum(1 for r in res if r.get("status") == "failed"),
                       s.get("healed_steps", 0), None, s.get("video"), shots[-1] if shots else None, err,
                       existing and existing["iteration"], existing and existing["batch_id"], existing and existing["config_json"], created,
                       (existing and existing["data_json"]) or _read(Path(run_dir) / "data.json"),
                       (existing and existing["sensitive_json"]) or json.dumps(_script_sensitive(Path(run_dir))),
                       existing and existing["source_run"],
                       (existing and existing["parser"]) or _parser_of(Path(run_dir), s.get("mode")),
                       existing and existing["owner"], (existing and existing["project"]) or "default"))
            c.execute("DELETE FROM steps WHERE run_id=?", (s["run_id"],))
            for r in res:
                st = r.get("step", {})
                loc = st.get("locator")
                c.execute("""INSERT OR REPLACE INTO steps (run_id, idx, status, raw, action, target, value, locator, error, screenshot,
                             url, duration_ms, healed, dialog, parsed_by, picked_by, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (s["run_id"], r["index"], r.get("status"), st.get("raw"), st.get("action"), st.get("target"),
                           r.get("shown_value"), _loc_label(loc), r.get("error"), r.get("screenshot"), r.get("url"),
                           r.get("duration_ms"), 1 if r.get("healed") else 0, r.get("dialog"), st.get("source"), r.get("picked_by"),
                           st.get("origin")))

    def import_artifacts(self, root: Path) -> int:
        """Pick up run folders that aren't in the DB yet (e.g. created before the DB existed)."""
        known = {r["run_id"] for r in self.q("SELECT run_id FROM runs")}
        n = 0
        for p in Path(root).glob("*/summary.json"):
            if p.parent.name not in known:
                try:
                    self.finalize_from_summary(p.parent)
                    n += 1
                except Exception:
                    pass
        return n

    def backfill_parser(self, root: Path) -> None:
        """Older rows have no 'parser': work it out from their script.json so badges are truthful."""
        for r in self.q("SELECT run_id, mode FROM runs WHERE parser IS NULL"):
            self.update_run(r["run_id"], parser=_parser_of(Path(root) / r["run_id"], r["mode"]))

    vault = None          # set by the hub: secrets kept for replays are encrypted at rest

    def put_secrets(self, run_id: str, secrets: dict) -> None:
        if secrets:
            raw = ("enc:" + self.vault.enc(secrets)) if self.vault else json.dumps(secrets)
            self.x("INSERT OR REPLACE INTO run_secrets (run_id, secrets_json) VALUES (?,?)", (run_id, raw))

    def get_secrets(self, run_id: str) -> dict:
        r = self.one("SELECT secrets_json FROM run_secrets WHERE run_id=?", (run_id,))
        if not r:
            return {}
        raw = r["secrets_json"]
        if raw.startswith("enc:"):
            return (self.vault.dec(raw[4:], {}) if self.vault else {}) or {}
        return json.loads(raw)           # stored before encryption existed

    def batch_runs(self, batch_id: str) -> list[dict]:
        return self.q("SELECT * FROM runs WHERE batch_id=? ORDER BY created", (batch_id,))

    def raw_run(self, run_id: str) -> dict | None:
        return self.one("SELECT * FROM runs WHERE run_id=?", (run_id,))

    def list_runs(self, limit: int = 200, status: str | None = None, q: str | None = None,
                  owner: str | None = None, project: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM runs WHERE 1=1", []
        if project:
            sql += " AND COALESCE(project, 'default')=?"
            args.append(project)
        if owner:
            sql += " AND owner=?"
            args.append(owner)
        if status:
            sql += " AND status=?"
            args.append(status)
        if q:
            sql += " AND (test_name LIKE ? OR agent LIKE ? OR engine LIKE ?)"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        return [_run_out(r) for r in self.q(sql, args)]

    def get_run(self, run_id: str) -> dict | None:
        r = self.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not r:
            return None
        out = _run_out(r)
        out["steps"] = self.q("SELECT * FROM steps WHERE run_id=? ORDER BY idx", (run_id,))
        out["data"] = json.loads(r["data_json"]) if r.get("data_json") else {}
        out["sensitive"] = json.loads(r["sensitive_json"]) if r.get("sensitive_json") else []
        out["config"] = json.loads(r["config_json"]) if r.get("config_json") else {}
        out["has_secrets"] = bool(self.get_secrets(run_id))
        out["source_run"] = r.get("source_run")
        if r.get("batch_id"):
            out["batch"] = [_run_out(b) for b in self.batch_runs(r["batch_id"])]
        return out

    def delete_run(self, run_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM steps WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM run_secrets WHERE run_id=?", (run_id,))

    def stats(self, project: str | None = None) -> dict:
        where, args = ("WHERE COALESCE(project, 'default')=?", [project]) if project else ("", [])
        r = self.one("""SELECT COUNT(*) total,
                          SUM(status='passed') passed, SUM(status='failed') failed,
                          SUM(status IN ('queued','dispatched','running')) live,
                          AVG(CASE WHEN status IN ('passed','failed') THEN duration_ms END) avg_ms,
                          SUM(healed) healed,
                          SUM(created > ?) day, SUM(created > ? AND status='failed') day_failed
                        FROM runs """ + where, (time.time() - 86400, time.time() - 86400, *args)) or {}
        trend = self.q("""SELECT date(created, 'unixepoch') d, SUM(status='passed') passed, SUM(status='failed') failed
                          FROM runs WHERE created > ? """ + (where.replace("WHERE", "AND") if where else "") +
                       " GROUP BY d ORDER BY d", (time.time() - 14 * 86400, *args))
        return {k: (v or 0) for k, v in r.items()} | {"trend": trend}

    # ------------------------------------------------------------------ library
    def lib_put(self, name: str, test: dict, source_run: str | None = None, owner: str | None = None,
                project: str | None = None) -> dict:
        now = time.time()
        old = self.one("SELECT created, owner, project FROM library WHERE name=?", (name,))
        self.x("""INSERT OR REPLACE INTO library (name, test_name, steps, engine, sensitive, test_json, source_run, created, updated, owner, project)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (name, test.get("name"), len(test.get("steps", [])), test.get("engine"), json.dumps(test.get("sensitive", [])),
                json.dumps(test), source_run, old["created"] if old else now, now, (old and old["owner"]) or owner,
                (old and old["project"]) or project or "default"))
        return self.lib_meta(name)

    def lib_meta(self, name: str) -> dict | None:
        r = self.one("SELECT name, test_name, steps, engine, sensitive, source_run, created, updated, test_json, owner, project FROM library WHERE name=?", (name,))
        if not r:
            return None
        t = json.loads(r.pop("test_json"))
        r["sensitive"] = json.loads(r["sensitive"] or "[]")
        r["data_keys"] = sorted((t.get("data") or {}).keys())
        return r

    def lib_list(self, project: str | None = None) -> list[dict]:
        sql, args = "SELECT name FROM library", []
        if project:
            sql += " WHERE COALESCE(project, 'default')=?"
            args.append(project)
        return [self.lib_meta(r["name"]) for r in self.q(sql + " ORDER BY updated DESC", args)]

    def lib_get(self, name: str) -> dict | None:
        r = self.one("SELECT test_json FROM library WHERE name=?", (name,))
        return json.loads(r["test_json"]) if r else None

    def lib_delete(self, name: str) -> None:
        self.x("DELETE FROM library WHERE name=?", (name,))

    # ------------------------------------------------------------------ sessions
    def session_put(self, name: str, state: dict, owner: str | None = None, project: str | None = None) -> None:
        domains = sorted({c.get("domain", "").lstrip(".") for c in state.get("cookies", []) if c.get("domain")})
        origins = [o.get("origin", "") for o in state.get("origins", [])]
        old = self.one("SELECT owner, project FROM sessions WHERE name=?", (name,))
        self.x("INSERT OR REPLACE INTO sessions (name, state_json, domains, updated, owner, project) VALUES (?,?,?,?,?,?)",
               (name, json.dumps(state), json.dumps((domains + origins)[:12]), time.time(), owner or (old and old["owner"]),
                project or (old and old["project"]) or "default"))

    def session_owner(self, name: str) -> str | None:
        r = self.one("SELECT owner FROM sessions WHERE name=?", (name,))
        return r["owner"] if r else None

    def setting_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(r["value_json"]) if r else default

    def setting_put(self, key: str, value: Any) -> None:
        self.x("INSERT OR REPLACE INTO settings (key, value_json, updated) VALUES (?,?,?)", (key, json.dumps(value), time.time()))

    def session_get(self, name: str) -> dict | None:
        r = self.one("SELECT state_json FROM sessions WHERE name=?", (name,))
        return json.loads(r["state_json"]) if r else None

    def session_list(self, owner: str | None = None, project: str | None = None) -> list[dict]:
        sql, args = "SELECT name, domains, updated, owner FROM sessions WHERE 1=1", []
        if owner is not None:            # a user sees their own saved logins (and old ones nobody owns)
            sql += " AND (owner=? OR owner IS NULL)"
            args.append(owner)
        if project:
            sql += " AND COALESCE(project, 'default')=?"
            args.append(project)
        return [{"name": r["name"], "domains": json.loads(r["domains"] or "[]"), "updated": int(r["updated"]), "owner": r["owner"]}
                for r in self.q(sql + " ORDER BY updated DESC", args)]

    # ------------------------------------------------------------------ projects
    def projects(self) -> list[dict]:
        rows = self.q("SELECT key, name, description, created FROM projects ORDER BY key='default' DESC, name")
        for r in rows:
            r["members"] = [m["username"] for m in self.q(
                "SELECT u.username FROM project_members pm JOIN users u ON u.id=pm.user_id WHERE pm.project=? ORDER BY u.username", (r["key"],))]
            r["runs"] = self.one("SELECT COUNT(*) n FROM runs WHERE COALESCE(project,'default')=?", (r["key"],))["n"]
        return rows

    def project(self, key: str) -> dict | None:
        return next((p for p in self.projects() if p["key"] == key), None)

    def project_put(self, key: str, name: str, description: str | None = None) -> None:
        old = self.one("SELECT created FROM projects WHERE key=?", (key,))
        self.x("INSERT OR REPLACE INTO projects (key, name, description, created) VALUES (?,?,?,?)",
               (key, name, description, old["created"] if old else time.time()))

    def project_members_set(self, key: str, user_ids: list[int]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM project_members WHERE project=?", (key,))
            c.executemany("INSERT INTO project_members (project, user_id) VALUES (?,?)", [(key, u) for u in user_ids])

    def project_delete(self, key: str) -> None:
        """Its runs, scripts and saved logins move to the default project (nothing is lost)."""
        with self.tx() as c:
            for t in ("runs", "library", "sessions"):
                c.execute(f"UPDATE {t} SET project='default' WHERE project=?", (key,))
            c.execute("DELETE FROM project_members WHERE project=?", (key,))
            c.execute("DELETE FROM projects WHERE key=?", (key,))

    def user_projects(self, user_id: int) -> set[str]:
        """Projects a user may see: every project they're a member of, plus the default one."""
        return {"default"} | {r["project"] for r in self.q("SELECT project FROM project_members WHERE user_id=?", (user_id,))}

    def run_project(self, run_id: str) -> str | None:
        r = self.one("SELECT COALESCE(project, 'default') p FROM runs WHERE run_id=?", (run_id,))
        return r["p"] if r else None

    # ------------------------------------------------------------------ users
    def user_get(self, user_id: int | None = None, username: str | None = None) -> dict | None:
        if user_id is not None:
            return self.one("SELECT * FROM users WHERE id=?", (user_id,))
        return self.one("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,))

    def user_by_sso(self, issuer: str, sub: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE sso_issuer=? AND sso_sub=?", (issuer, sub))

    def user_by_email(self, email: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)) if email else None

    def users(self) -> list[dict]:
        return self.q("SELECT id, username, name, email, role, disabled, created, last_login, sso_issuer, "
                      "pw_hash IS NOT NULL AS has_password FROM users ORDER BY username")

    def user_count(self) -> int:
        return self.one("SELECT COUNT(*) AS n FROM users")["n"]

    def user_create(self, username: str, role: str, pw_hash: str | None = None, name: str | None = None,
                    email: str | None = None, sso_issuer: str | None = None, sso_sub: str | None = None) -> dict:
        self.x("INSERT INTO users (username, name, email, role, pw_hash, sso_issuer, sso_sub, created) VALUES (?,?,?,?,?,?,?,?)",
               (username, name, email, role, pw_hash, sso_issuer, sso_sub, time.time()))
        return self.user_get(username=username)

    def user_update(self, user_id: int, **fields) -> None:
        if fields:
            self.x(f"UPDATE users SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), user_id))

    def user_delete(self, user_id: int) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM user_ai WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM users WHERE id=?", (user_id,))

    def auth_session_put(self, token_hash: str, user_id: int, expires: float) -> None:
        self.x("INSERT INTO auth_sessions (token_hash, user_id, created, expires) VALUES (?,?,?,?)",
               (token_hash, user_id, time.time(), expires))

    def auth_session_user(self, token_hash: str) -> dict | None:
        r = self.one("SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?",
                     (token_hash, time.time()))
        return r if r and not r["disabled"] else None

    def auth_session_delete(self, token_hash: str | None = None, user_id: int | None = None) -> None:
        if token_hash:
            self.x("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
        if user_id is not None:
            self.x("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        self.x("DELETE FROM auth_sessions WHERE expires<?", (time.time(),))

    def agent_token_add(self, label: str, token_hash: str, created_by: str | None) -> int:
        self.x("INSERT INTO agent_tokens (label, token_hash, created, created_by) VALUES (?,?,?,?)",
               (label, token_hash, time.time(), created_by))
        return self.one("SELECT id FROM agent_tokens WHERE token_hash=?", (token_hash,))["id"]

    def agent_token_find(self, token_hash: str) -> dict | None:
        return self.one("SELECT * FROM agent_tokens WHERE token_hash=? AND revoked=0", (token_hash,))

    def agent_token_used(self, token_id: int, machine: str | None) -> None:
        self.x("UPDATE agent_tokens SET last_used=?, last_machine=COALESCE(?, last_machine) WHERE id=?", (time.time(), machine, token_id))

    def agent_tokens(self) -> list[dict]:
        return self.q("SELECT id, label, created, created_by, last_used, last_machine, revoked FROM agent_tokens ORDER BY created DESC")

    def agent_token_revoke(self, token_id: int) -> None:
        self.x("UPDATE agent_tokens SET revoked=1 WHERE id=?", (token_id,))

    def agent_token_count(self) -> int:
        return self.one("SELECT COUNT(*) AS n FROM agent_tokens WHERE revoked=0")["n"]

    def user_ai_get(self, user_id: int) -> str | None:
        r = self.one("SELECT data_enc FROM user_ai WHERE user_id=?", (user_id,))
        return r["data_enc"] if r else None

    def user_ai_put(self, user_id: int, data_enc: str) -> None:
        self.x("INSERT OR REPLACE INTO user_ai (user_id, data_enc, updated) VALUES (?,?,?)", (user_id, data_enc, time.time()))

    def session_delete(self, name: str) -> None:
        self.x("DELETE FROM sessions WHERE name=?", (name,))

    # ------------------------------------------------------------------ machines
    def machine_seen(self, name: str, info: dict, online: bool) -> None:
        now = time.time()
        old = self.one("SELECT first_seen FROM machines WHERE name=?", (name,))
        self.x("""INSERT OR REPLACE INTO machines (name, hostname, platform, engines, browsers, version, first_seen, last_seen, online)
                  VALUES (?,?,?,?,?,?,?,?,?)""",
               (name, info.get("hostname"), info.get("platform"), json.dumps(info.get("engines") or []),
                json.dumps(info.get("browsers") or []), info.get("version"), old["first_seen"] if old else now, now, 1 if online else 0))

    def machine_offline(self, name: str) -> None:
        self.x("UPDATE machines SET online=0, last_seen=? WHERE name=?", (time.time(), name))

    def machines(self) -> list[dict]:
        out = []
        for r in self.q("SELECT * FROM machines ORDER BY online DESC, last_seen DESC"):
            r["engines"] = json.loads(r["engines"] or "[]")
            r["browsers"] = json.loads(r["browsers"] or "[]")
            r["online"] = bool(r["online"])
            out.append(r)
        return out

    def machine_delete(self, name: str) -> None:
        self.x("DELETE FROM machines WHERE name=?", (name,))


def _parser_of(run_dir: Path, mode: str | None) -> str:
    if mode == "replay":
        return "script"
    try:
        srcs = {st.get("source") for st in json.loads((run_dir / "script.json").read_text(encoding="utf-8")).get("steps", [])}
        if "ai" in srcs:
            return "ai"
        if "user" in srcs:
            return "user"
        return "rules"          # includes runs recorded before provenance existed (AI was not configured)
    except Exception:
        return "rules"


def _read(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.exists() else None


def _script_sensitive(run_dir: Path) -> list:
    try:
        return json.loads((run_dir / "script.json").read_text(encoding="utf-8")).get("sensitive", [])
    except Exception:
        return []


def _loc_label(loc: dict | None) -> str | None:
    if not loc:
        return None
    where = "".join(f"iframe[{f}] » " for f in loc.get("frames") or []) + "".join(f"shadow[{s}] » " for s in loc.get("shadow") or [])
    role = f"{loc['role']}:" if loc.get("role") else ""
    return f"{where}{loc['strategy']}={role}{loc['value']}"


def _run_out(r: dict) -> dict:
    r = dict(r)
    cfg = r.pop("config_json", None)
    for k in ("data_json", "sensitive_json"):
        r.pop(k, None)
    if cfg:
        try:
            c = json.loads(cfg)
            r["headless"] = c.get("headless")
            r["browser"] = r.get("browser") or c.get("browser")
        except Exception:
            pass
    r["healed_steps"] = r.get("healed", 0)
    r["passed_steps"] = r.get("passed", 0)
    r["failed_steps"] = r.get("failed", 0)
    r["live"] = r.get("status") in LIVE
    return r


def open_db() -> DB:
    return DB(os.getenv("AIAUTO_DB", "aiauto.db"))
