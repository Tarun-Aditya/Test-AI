"""Remote execution agent.

Run on any machine that has browsers:

    python -m aiauto agent --server https://hub.example.com --name my-laptop [--token SECRET]

The agent keeps an outbound WebSocket to the hub (works behind NAT/firewalls), receives jobs,
runs them with the local Playwright/Selenium, streams live step events back and uploads the
artifacts (report, video, screenshots, generated scripts) when each run finishes.
"""
from __future__ import annotations

import io
import json
import logging
import platform
import socket
import time
import zipfile
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


def _zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    return buf.getvalue()


def run_agent(server: str, name: str | None, token: str | None, out_dir: str) -> None:
    from websockets.sync.client import connect

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
                                    "version": "0.1.0"}))
                welcome = json.loads(ws.recv())
                if welcome.get("type") == "error":
                    log.error("hub refused connection: %s", welcome.get("message"))
                    return
                log.info("connected to %s as '%s' - waiting for jobs", server, welcome["name"])
                backoff = 1
                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "job":
                        continue
                    _handle_job(ws, msg["job"], server, token, out, welcome["name"])
        except KeyboardInterrupt:
            return
        except Exception as e:
            log.warning("connection lost (%s); retrying in %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _handle_job(ws, job: dict, server: str, token: str | None, out: Path, agent_name: str) -> None:
    run_id = job["run_id"]
    log.info("job %s received", run_id)

    def send_event(ev: dict) -> None:
        try:
            ws.send(json.dumps({"type": "event", "event": ev}))
        except Exception:
            pass

    test = TestCase.model_validate(job["test"])
    cfg = RunConfig.model_validate(job["config"])
    final = {}

    def on_event(ev: dict) -> None:
        if ev["type"] == "run_finished":
            final.update(ev)       # hold back until artifacts are uploaded
        else:
            send_event(ev)

    summary = None
    try:
        summary = run_test(test, cfg, out, mode=job.get("mode", "ai"), on_event=on_event,
                           agent=agent_name, run_id=run_id)
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
