"""Hub automation: scheduled runs (cron), CI hooks, failure notifications and flaky-test detection.

Everything is stored in the hub's `settings` table as small JSON documents, so there is nothing to migrate:
  schedules      [{id, name, library, cron, enabled, data, config, project, owner, last_run, last_run_ids}]
  hooks          [{id, name, library, token_hash, data, config, project, owner, last_run}]
  notifications  [{id, kind: slack|teams|webhook|email, url|to, on: failed|always|recovered, project}]
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import smtplib
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable

import httpx

log = logging.getLogger("aiauto.automation")


# ------------------------------------------------------------------------------------------------ cron
class CronError(ValueError):
    pass


_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_SHORT = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
          "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}


def _field(text: str, lo: int, hi: int, names: dict | None = None) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            if not s.isdigit() or int(s) < 1:
                raise CronError(f"bad step in '{text}'")
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a_s, _, b_s = part.partition("-")
            a, b = _num(a_s, names), _num(b_s, names)
        else:
            a = _num(part, names)
            b = hi if step > 1 else a
        if a < lo or b > hi or a > b:
            raise CronError(f"'{part}' is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def _num(s: str, names: dict | None) -> int:
    s = s.strip().lower()
    if names and s in names:
        return names[s]
    if not s.isdigit():
        raise CronError(f"'{s}' is not a number")
    return int(s)


class Cron:
    """Five fields: minute hour day-of-month month day-of-week (0 or 7 = Sunday). Lists, ranges and steps; @daily etc."""

    def __init__(self, expr: str):
        e = _SHORT.get(expr.strip().lower(), expr).split()
        if len(e) != 5:
            raise CronError("a schedule has five fields: minute hour day-of-month month day-of-week")
        self.expr = " ".join(e)
        self.minutes = _field(e[0], 0, 59)
        self.hours = _field(e[1], 0, 23)
        self.days = _field(e[2], 1, 31)
        self.months = _field(e[3], 1, 12, {n: i for i, n in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)})
        dows = _field(e[4].replace("7", "0") if e[4] == "7" else e[4], 0, 7, _NAMES)
        self.dows = {d % 7 for d in dows}
        self._dom_any, self._dow_any = e[2].startswith("*"), e[4].startswith("*")

    def matches(self, t: datetime) -> bool:
        if t.minute not in self.minutes or t.hour not in self.hours or t.month not in self.months:
            return False
        dom, dow = t.day in self.days, ((t.weekday() + 1) % 7) in self.dows
        if self._dom_any or self._dow_any:        # classic cron: one restricted field decides; both restricted: either
            return dom and dow
        return dom or dow

    def next_after(self, t: datetime) -> datetime:
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if self.matches(t):
                return t
            t += timedelta(minutes=1)
        raise CronError("this schedule never fires")


# ------------------------------------------------------------------------------------------------ stores
def _load(db, key: str) -> list[dict]:
    return list(db.setting_get(key, []) or [])


def _save(db, key: str, items: list[dict]) -> None:
    db.setting_put(key, items)


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("token_hash",)}


class Schedules:
    def __init__(self, db):
        self.db = db

    def list(self, projects=None) -> list[dict]:
        items = _load(self.db, "schedules")
        out = []
        for s in items:
            if projects is not None and s.get("project", "default") not in projects:
                continue
            d = _public(s)
            try:
                d["next_run"] = Cron(s["cron"]).next_after(datetime.now(timezone.utc).astimezone()).isoformat(timespec="minutes") if s.get("enabled", True) else None
            except CronError:
                d["next_run"] = None
            out.append(d)
        return out

    def put(self, item: dict) -> dict:
        Cron(item["cron"])                                   # validates
        items = _load(self.db, "schedules")
        item = {**item, "id": item.get("id") or uuid.uuid4().hex[:8], "enabled": item.get("enabled", True)}
        items = [i for i in items if i["id"] != item["id"]] + [item]
        _save(self.db, "schedules", items)
        return item

    def get(self, sid: str) -> dict | None:
        return next((i for i in _load(self.db, "schedules") if i["id"] == sid), None)

    def delete(self, sid: str) -> bool:
        items = _load(self.db, "schedules")
        keep = [i for i in items if i["id"] != sid]
        _save(self.db, "schedules", keep)
        return len(keep) != len(items)

    def due(self, now: datetime) -> list[dict]:
        """Schedules whose minute is this one and that have not run in it yet (a restart never double-fires)."""
        stamp = now.strftime("%Y-%m-%d %H:%M")
        out = []
        for s in _load(self.db, "schedules"):
            if not s.get("enabled", True) or s.get("last_fired") == stamp:
                continue
            try:
                if Cron(s["cron"]).matches(now):
                    out.append(s)
            except CronError:
                log.warning("schedule %s has an invalid cron: %s", s.get("id"), s.get("cron"))
        return out

    def mark(self, sid: str, **fields) -> None:
        items = _load(self.db, "schedules")
        for i in items:
            if i["id"] == sid:
                i.update(fields)
        _save(self.db, "schedules", items)


class Hooks:
    """A URL a CI system calls to run a saved script: no sign-in, the secret is in the URL path."""

    def __init__(self, db):
        self.db = db

    def list(self, projects=None) -> list[dict]:
        return [_public(h) for h in _load(self.db, "hooks") if projects is None or h.get("project", "default") in projects]

    def create(self, item: dict) -> tuple[dict, str]:
        token = secrets.token_urlsafe(24)
        item = {**item, "id": uuid.uuid4().hex[:8], "token_hash": hashlib.sha256(token.encode()).hexdigest()}
        _save(self.db, "hooks", _load(self.db, "hooks") + [item])
        return _public(item), token

    def find(self, token: str) -> dict | None:
        h = hashlib.sha256(token.encode()).hexdigest()
        return next((i for i in _load(self.db, "hooks") if secrets.compare_digest(i["token_hash"], h)), None)

    def delete(self, hid: str) -> bool:
        items = _load(self.db, "hooks")
        keep = [i for i in items if i["id"] != hid]
        _save(self.db, "hooks", keep)
        return len(keep) != len(items)

    def mark(self, hid: str, **fields) -> None:
        items = _load(self.db, "hooks")
        for i in items:
            if i["id"] == hid:
                i.update(fields)
        _save(self.db, "hooks", items)


# ------------------------------------------------------------------------------------------------ notifications
KINDS = ("slack", "teams", "webhook", "email")


def message(run: dict, base_url: str = "") -> dict:
    """What a notification says about one finished run."""
    status = run.get("status", "?")
    name = run.get("test_name") or run.get("name") or run.get("run_id")
    steps = f"{run.get('passed', 0)}/{run.get('total', 0)} steps passed"
    why = ""
    if status == "failed":
        why = (run.get("error") or "") or ""
    link = f"{base_url.rstrip('/')}/#/runs/{run.get('run_id')}" if base_url else ""
    icon = {"passed": "✅", "failed": "❌"}.get(status, "⚠️")
    text = f"{icon} {name}: {status} ({steps})" + (f"\n{why}" if why else "") + (f"\n{link}" if link else "")
    return {"text": text, "status": status, "name": name, "run_id": run.get("run_id"), "steps": steps, "error": why, "url": link,
            "project": run.get("project") or "default", "engine": run.get("engine"), "browser": run.get("browser")}


def _should(rule: dict, msg: dict, previous: str | None) -> bool:
    if rule.get("project") and rule["project"] != msg["project"]:
        return False
    when = rule.get("on", "failed")
    if when == "always":
        return True
    if when == "recovered":
        return msg["status"] == "passed" and previous == "failed"
    return msg["status"] in ("failed", "interrupted")


def post_json(url: str, payload: dict, timeout: float = 10.0) -> None:
    r = httpx.post(url, json=payload, timeout=timeout, follow_redirects=True)
    if r.status_code >= 400:
        raise RuntimeError(f"{url.split('?')[0]} answered {r.status_code}")


def send_email(rule: dict, msg: dict, smtp: dict) -> None:
    host = smtp.get("host")
    if not host:
        raise RuntimeError("no SMTP server set (AIAUTO_SMTP_HOST)")
    m = EmailMessage()
    m["Subject"] = f"[Autopilot] {msg['name']}: {msg['status']}"
    m["From"] = smtp.get("from") or smtp.get("user") or "autopilot@localhost"
    m["To"] = rule["to"]
    m.set_content(msg["text"])
    with smtplib.SMTP(host, int(smtp.get("port") or 587), timeout=15) as s:
        if smtp.get("tls", True):
            s.starttls(context=ssl.create_default_context())
        if smtp.get("user"):
            s.login(smtp["user"], smtp.get("password") or "")
        s.send_message(m)


def deliver(rule: dict, msg: dict, smtp: dict | None = None) -> None:
    kind = rule["kind"]
    if kind == "slack":
        post_json(rule["url"], {"text": msg["text"]})
    elif kind == "teams":
        post_json(rule["url"], {"@type": "MessageCard", "@context": "https://schema.org/extensions", "summary": msg["text"].splitlines()[0],
                                "themeColor": "2EB67D" if msg["status"] == "passed" else "E01E5A", "text": msg["text"].replace("\n", "<br>")})
    elif kind == "webhook":
        post_json(rule["url"], {k: v for k, v in msg.items() if k != "text"} | {"text": msg["text"]})
    elif kind == "email":
        send_email(rule, msg, smtp or {})
    else:
        raise RuntimeError(f"unknown channel '{kind}'")


class Notifier:
    def __init__(self, db, smtp: Callable[[], dict] | None = None, base_url: Callable[[], str] | None = None):
        self.db, self._smtp, self._base = db, smtp or (lambda: {}), base_url or (lambda: "")

    def list(self) -> list[dict]:
        return _load(self.db, "notifications")

    def put(self, rule: dict) -> dict:
        kind = rule.get("kind")
        if kind not in KINDS:
            raise ValueError(f"channel must be one of {', '.join(KINDS)}")
        if kind == "email" and not rule.get("to"):
            raise ValueError("an email channel needs 'to'")
        if kind != "email" and not str(rule.get("url", "")).startswith(("http://", "https://")):
            raise ValueError("a channel needs the http(s) URL of the webhook")
        if rule.get("on", "failed") not in ("failed", "always", "recovered"):
            raise ValueError("'on' must be failed, always or recovered")
        rule = {**rule, "id": rule.get("id") or uuid.uuid4().hex[:8], "on": rule.get("on", "failed")}
        _save(self.db, "notifications", [r for r in self.list() if r["id"] != rule["id"]] + [rule])
        return rule

    def delete(self, rid: str) -> bool:
        items = self.list()
        keep = [r for r in items if r["id"] != rid]
        _save(self.db, "notifications", keep)
        return len(keep) != len(items)

    def previous_status(self, run: dict) -> str | None:
        rows = [r for r in self.db.list_runs(limit=30, q=run.get("test_name")) if r["run_id"] != run.get("run_id") and r.get("test_name") == run.get("test_name")
                and r.get("status") in ("passed", "failed")]
        return rows[0]["status"] if rows else None

    def run_finished(self, run: dict) -> list[dict]:
        """Tell every channel that wants to know. Returns what was done (for the log and the tests)."""
        rules = self.list()
        if not rules:
            return []
        msg = message(run, self._base())
        prev = self.previous_status(run) if any(r.get("on") == "recovered" for r in rules) else None
        out = []
        for r in rules:
            if not _should(r, msg, prev):
                continue
            try:
                deliver(r, msg, self._smtp())
                out.append({"id": r["id"], "ok": True})
            except Exception as e:
                log.warning("notification %s failed: %s", r["id"], e)
                out.append({"id": r["id"], "ok": False, "error": str(e)[:200]})
        return out


# ------------------------------------------------------------------------------------------------ flaky tests
def flaky_report(runs: list[dict], window: int = 20, min_runs: int = 4) -> list[dict]:
    """Per test: how often it passes, and how often the result flips between neighbouring runs.

    A test is flaky when it both passes and fails over its recent runs and the result keeps flipping (a test that
    broke once and stayed broken is failing, not flaky). `runs` are newest-first, as `DB.list_runs` returns them.
    Iterations of one data row are separate tests ("name · #2"), so each is judged on its own history.
    """
    by: dict[str, list[dict]] = {}
    for r in runs:
        if r.get("status") in ("passed", "failed"):
            by.setdefault(r.get("test_name") or "?", []).append(r)
    out = []
    for name, rs in by.items():
        recent = rs[:window]
        seq = [r["status"] for r in reversed(recent)]          # oldest first
        flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
        passed, failed = seq.count("passed"), seq.count("failed")
        healed = sum(1 for r in recent if (r.get("healed") or 0) > 0)
        flaky = len(seq) >= min_runs and passed > 0 and failed > 0 and flips >= 2
        out.append({"name": name, "runs": len(seq), "passed": passed, "failed": failed,
                    "pass_rate": round(passed / len(seq), 3) if seq else None, "flips": flips,
                    "flaky": flaky, "score": round(flips / max(1, len(seq) - 1), 3),
                    "last_status": seq[-1] if seq else None, "healed_runs": healed,
                    "last_run": recent[0].get("run_id") if recent else None,
                    "history": seq})
    out.sort(key=lambda x: (not x["flaky"], -x["score"], x["name"]))
    return out
