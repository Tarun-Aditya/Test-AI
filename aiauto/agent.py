"""Remote execution agent.

Run on any machine that has browsers:

    python -m aiauto agent --server https://hub.example.com --name my-laptop [--token SECRET] [--slots 3]

The agent keeps an outbound WebSocket to the hub (works behind NAT/firewalls), receives jobs,
runs them with the local Playwright/Selenium, streams live step events back and uploads the
artifacts (report, video, screenshots, generated scripts) when each run finishes.

--slots N runs up to N jobs at the same time (each in its own browser, any mix of Playwright and
Selenium). The hub never sends more jobs than the agent has free slots.
"""
from __future__ import annotations

import io
import json
import logging
import platform
import os
import socket
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .models import RunConfig, TestCase
from .runner import run_test

log = logging.getLogger("aiauto.agent")


def _engines() -> list[str]:
    out = []
    for mod, name in (("playwright", "playwright"), ("selenium", "selenium")):
        try:
            __import__(mod)
            out.append(name)
        except ImportError:
            pass
    return out


def _browsers() -> list[str]:
    import shutil
    found = ["chromium (bundled)"]
    for name, exe in (("chrome", "google-chrome"), ("chrome", "chrome"), ("msedge", "microsoft-edge"), ("firefox", "firefox")):
        if shutil.which(exe) and name not in found:
            found.append(name)
    return found


def _local_ai() -> dict:
    """AI settings set on this machine win over the hub's (e.g. its own API key or CLI path)."""
    import os
    m = {"AIAUTO_AI_PROVIDER": "provider", "AIAUTO_MODEL": "model", "AIAUTO_LITELLM_MODEL": "litellm_model",
         "AIAUTO_LITELLM_BASE": "litellm_base", "AIAUTO_LITELLM_KEY": "litellm_key", "ANTHROPIC_API_KEY": "anthropic_key"}
    return {v: os.environ[k] for k, v in m.items() if os.getenv(k)}


def _zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    return buf.getvalue()


class _Holder(list):
    """Receives the job's Runner; a cancel that arrives before the Runner exists is applied when it does."""

    def __init__(self):
        super().__init__()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        for r in self:
            r.cancel.set()

    def append(self, r):
        if self.cancelled:
            r.cancel.set()
        super().append(r)


class _Link:
    """The current hub connection, shared by the job threads (sends are serialized; a reconnect swaps it)."""

    def __init__(self):
        self.ws = None
        self.lock = threading.Lock()

    def send(self, msg: dict) -> None:
        with self.lock:
            if self.ws is not None:
                self.ws.send(json.dumps(msg))


def run_agent(server: str, name: str | None, token: str | None, out_dir: str, slots: int | None = None) -> None:
    from websockets.sync.client import connect

    slots = max(1, int(slots or os.getenv("AIAUTO_SLOTS") or 1))
    workers = ThreadPoolExecutor(max_workers=slots, thread_name_prefix="job")
    link = _Link()
    runners: dict[str, list] = {}           # run id -> [Runner] of the jobs in progress (for cancel)

    server = server.rstrip("/")
    ws_url = server.replace("https://", "wss://").replace("http://", "ws://") + "/ws/agent"
    name = name or socket.gethostname()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    backoff = 1
    while True:
        try:
            with connect(ws_url, open_timeout=15, ping_interval=20, ping_timeout=60, max_size=None) as ws:
                ws.send(json.dumps({"type": "hello", "name": name, "token": token, "hostname": socket.gethostname(),
                                    "platform": f"{platform.system()} {platform.release()}", "engines": _engines(),
                                    "browsers": _browsers(), "slots": slots,
                                    "version": "0.2.0"}))
                welcome = json.loads(ws.recv())
                if welcome.get("type") == "error":
                    log.error("hub refused connection: %s", welcome.get("message"))
                    return
                log.info("connected to %s as '%s' with %d slot(s) - waiting for jobs", server, welcome["name"], slots)
                backoff = 1
                link.ws = ws
                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "cancel":
                        h = runners.get(msg.get("run_id"))
                        if h is not None:
                            h.cancel()
                            log.info("cancelling %s", msg["run_id"])
                        continue
                    if msg.get("type") != "job":
                        continue
                    runners[msg["job"]["run_id"]] = _Holder()
                    workers.submit(_handle_job, link, msg["job"], server, token, out, welcome["name"], runners)
        except KeyboardInterrupt:
            return
        except Exception as e:
            link.ws = None
            log.warning("connection lost (%s); retrying in %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _handle_job(link: _Link, job: dict, server: str, token: str | None, out: Path, agent_name: str,
                runners: dict | None = None) -> None:
    try:
        _do_job(link, job, server, token, out, agent_name, (runners or {}).get(job["run_id"], []))
    finally:
        (runners or {}).pop(job["run_id"], None)


def _do_job(link: _Link, job: dict, server: str, token: str | None, out: Path, agent_name: str, holder: list) -> None:
    run_id = job["run_id"]
    log.info("job %s received (%s)", run_id, job.get("config", {}).get("engine"))

    def send_event(ev: dict) -> None:
        try:
            link.send({"type": "event", "event": ev})
        except Exception:
            pass

    test = TestCase.model_validate(job["test"])
    cfg = RunConfig.model_validate(job["config"])
    cfg.session_state = job.get("session_state")
    if job.get("ai") is not None:        # the hub's AI choice; this machine's own CLIs / env keys still apply
        from .nlp import providers
        # without a usable provider here, AI calls go to the hub, which answers with its own (e.g. your
        # subscription logged in on the hub's computer)
        providers.configure({**job["ai"], **_local_ai(), "hub_url": server, "hub_token": token})
    final = {}

    def on_event(ev: dict) -> None:
        if ev["type"] == "run_finished":
            final.update(ev)       # hold back until artifacts are uploaded
        else:
            send_event(ev)

    summary = None
    try:
        summary = run_test(test, cfg, out, mode=job.get("mode", "ai"), on_event=on_event,
                           agent=agent_name, run_id=run_id, runner_out=holder)
        if cfg.save_session and holder and holder[0].saved_session:
            # session tokens go straight to the hub's session store, never into the artifacts zip
            link.send({"type": "session", "name": cfg.save_session, "state": holder[0].saved_session})
    except Exception as e:
        log.exception("run failed")
        final = {"type": "run_finished", "run_id": run_id, "status": "failed", "error": str(e)}

    run_dir = out / run_id
    if run_dir.exists():
        try:
            headers = {"x-agent-token": token} if token else {}
            r = httpx.post(f"{server}/api/agents/upload/{run_id}", headers=headers, timeout=300,
                           files={"file": (f"{run_id}.zip", _zip_dir(run_dir), "application/zip")})
            r.raise_for_status()
            log.info("artifacts uploaded for %s", run_id)
        except Exception as e:
            log.error("artifact upload failed: %s (kept locally in %s)", e, run_dir)
    send_event(final or {"type": "run_finished", "run_id": run_id,
                         "status": summary.status.value if summary else "failed"})
