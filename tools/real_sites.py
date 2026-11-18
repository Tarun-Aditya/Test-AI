"""Run the examples/real_sites scenarios against the real websites, on Playwright and Selenium, through a hub.

    python tools/real_sites.py [--only 01,07] [--workers 3] [--out real_sites_out]

Phase 1  first run   (English -> steps by the rule parser or AI, elements located on the live page)
Phase 2  replay      (each run's generated script: stored native locators, self-heal on)
Phase 3  exported    (the downloaded project's scripts run on their own, outside Autopilot)

Writes <out>/results.json and <out>/REPORT.md (metrics per scenario, engine, iteration and phase), and checks
that every run has its report, video, scripts and screenshots, and that the live view streamed frames."""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT / "examples" / "real_sites"
LIVE = ("queued", "dispatched", "running")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Events(threading.Thread):
    """A UI subscriber: counts live events per run (what the web app's live view receives)."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url, self.count, self.stop = url, defaultdict(Counter), False

    def run(self):
        from websockets.sync.client import connect
        while not self.stop:
            try:
                with connect(self.url, open_timeout=5, max_size=None) as ws:
                    while not self.stop:
                        try:
                            e = json.loads(ws.recv(timeout=1))
                        except TimeoutError:
                            continue
                        if e.get("run_id"):
                            self.count[e["run_id"]][e.get("type")] += 1
            except Exception:
                time.sleep(0.5)


def wait_done(c, ids, timeout=1800):
    end = time.time() + timeout
    while time.time() < end:
        rows = {r["run_id"]: r for r in c.get("/api/runs", params={"limit": 2000}).json()}
        if all(rows.get(i, {}).get("status") not in LIVE and i in rows for i in ids):
            return rows
        time.sleep(2)
    raise TimeoutError("runs did not finish")


def facts(art: Path, rid: str, ev: Counter) -> dict:
    d = art / rid
    s = json.loads((d / "summary.json").read_text(encoding="utf-8")) if (d / "summary.json").exists() else {}
    res = s.get("results", [])
    loc = [r["locate_ms"] for r in res if r.get("locate_ms") is not None]
    video = d / (s.get("video") or "video.webm")
    return {
        "run_id": rid, "status": s.get("status"), "duration_ms": s.get("duration_ms"), "steps": len(res),
        "passed": sum(r.get("status") == "passed" for r in res), "failed": sum(r.get("status") == "failed" for r in res),
        "skipped": sum(r.get("status") == "skipped" for r in res), "healed": s.get("healed_steps", 0),
        "locate_total_ms": sum(loc), "locate_median_ms": int(statistics.median(loc)) if loc else None,
        "locate_max_ms": max(loc) if loc else None,
        "error": next((f"step {r['index'] + 1} «{(r.get('step') or {}).get('raw', '')}»: {r.get('error', '')[:240]}"
                       for r in res if r.get("status") == "failed"), None),
        "skipped_optional": [f"step {r['index'] + 1}: {r.get('error', '')[:160]}" for r in res
                             if r.get("status") == "skipped" and r.get("error")],
        "artifacts": {"report": (d / "report.html").exists(), "script_json": (d / "script.json").exists(),
                      "test_playwright": (d / "test_playwright.py").exists(), "test_selenium": (d / "test_selenium.py").exists(),
                      "video_kb": round(video.stat().st_size / 1024) if video.exists() else 0,
                      "screenshots": len(list(d.rglob("*.png")))},
        "live": {"frames": ev.get("live_frame", 0), "step_shots": ev.get("step_shot", 0), "step_events": ev.get("step_finished", 0)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "real_sites_out"))
    ap.add_argument("--skip-export", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    art = out / "artifacts"
    port = free_port()
    env = {**os.environ, "AIAUTO_AUTH": "off", "AIAUTO_DB": str(out / "hub.db"), "AIAUTO_ARTIFACTS": str(art),
           "AIAUTO_LOCAL_WORKERS": str(a.workers), "PYTHONPATH": str(ROOT)}
    hub = subprocess.Popen([sys.executable, "-m", "aiauto", "server", "--host", "127.0.0.1", "--port", str(port)], cwd=out, env=env,
                           stdout=open(out / "hub.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                health = httpx.get(base + "/api/health", timeout=2).json()
                break
            except Exception:
                time.sleep(0.25)
        ev = Events(f"ws://127.0.0.1:{port}/ws/ui")
        ev.start()
        time.sleep(1)
        c = httpx.Client(base_url=base, timeout=120, headers={"X-Requested-With": "autopilot"})
        only = {x.strip() for x in a.only.split(",") if x.strip()}
        scen = [p for p in sorted(SCEN.glob("*.txt")) if not only or p.name[:2] in only]
        cfg = {"record_video": True, "live_view": True, "headless": True, "timeout_ms": 15000, "screenshot_each_step": True}
        first: dict[str, list[str]] = {}
        t0 = time.time()
        for p in scen:
            data = json.loads(p.with_suffix(".json").read_text(encoding="utf-8")) if p.with_suffix(".json").exists() else {}
            r = c.post("/api/runs", json={"text": p.read_text(encoding="utf-8"), "data": data, "name": p.stem.split("_", 1)[1].replace("_", " "),
                                          "engines": ["playwright", "selenium"], "config": cfg})
            if r.status_code != 200:
                print(p.name, "REFUSED", r.text[:300])
                continue
            first[p.stem] = r.json()["run_ids"]
            print(f"{p.stem}: {len(first[p.stem])} runs queued")
        rows = wait_done(c, [i for ids in first.values() for i in ids])
        print(f"phase 1 done in {time.time() - t0:.0f}s")

        replay: dict[str, str] = {}
        t1 = time.time()
        for ids in first.values():
            for rid in ids:
                eng = rows[rid]["engine"]
                r = c.post("/api/runs", json={"replay_run_id": rid, "config": {**cfg, "engine": eng}, "engines": [eng]})
                if r.status_code == 200:
                    replay[rid] = r.json()["run_ids"][0]
        rows2 = wait_done(c, list(replay.values()))
        print(f"phase 2 done in {time.time() - t1:.0f}s")

        result = {"health": health, "scenarios": {}}
        for stem, ids in first.items():
            runs = []
            for rid in ids:
                row = rows[rid]
                one = {"engine": row["engine"], "browser": row["browser"], "iteration": row.get("iteration"),
                       "first": facts(art, rid, ev.count[rid])}
                if rid in replay:
                    one["replay"] = facts(art, replay[rid], ev.count[replay[rid]])
                runs.append(one)
            result["scenarios"][stem] = runs

        if not a.skip_export:
            result["exported"] = exported(c, art, out, first, rows)
        ev.stop = True
        (out / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out / "REPORT.md").write_text(report(result), encoding="utf-8")
        print((out / "REPORT.md").read_text(encoding="utf-8"))
    finally:
        hub.terminate()
        try:
            hub.wait(15)
        except subprocess.TimeoutExpired:
            hub.kill()


def exported(c, art, out, first, rows):
    """Download each scenario's project once and run its scripts with no Autopilot around them."""
    res = {}
    for stem, ids in first.items():
        for eng in ("playwright", "selenium"):
            rid = next((i for i in ids if rows[i]["engine"] == eng and rows[i]["status"] == "passed"), None)
            if not rid:
                res[f"{stem} / {eng}"] = {"skipped": "no passed first run to export"}
                continue
            z = c.get(f"/api/runs/{rid}/export")
            if z.status_code != 200:
                res[f"{stem} / {eng}"] = {"error": f"export {z.status_code}"}
                continue
            d = out / "exported" / f"{stem}_{eng}"
            zipfile.ZipFile(io.BytesIO(z.content)).extractall(d)
            proj = next((p.parent for p in d.rglob("run.py")), d)
            env = {**os.environ, "AIAUTO_SECRET_PASSWORD": "secret_sauce", "AIAUTO_SECRET_PWD": "pwd", "HEADLESS": "1"}
            t = time.time()
            p = subprocess.run([sys.executable, "run.py", "--engine", eng, "--only", "1"], cwd=proj, env=env, capture_output=True, text=True, timeout=900)
            took = time.time() - t
            it = sorted((proj / "results").glob("iteration_*"))
            s = json.loads((it[0] / "summary.json").read_text(encoding="utf-8")) if it and (it[0] / "summary.json").exists() else {}
            res[f"{stem} / {eng}"] = {
                "exit": p.returncode, "seconds": round(took, 1), "status": s.get("status") or s.get("result"),
                "steps": len(s.get("results", s.get("steps", []))),
                "report": bool(it) and (it[0] / "report.html").exists(), "junit": bool(it) and (it[0] / "junit.xml").exists(),
                "video_kb": round(sum(f.stat().st_size for f in it[0].glob("*.webm")) / 1024) + round(sum(f.stat().st_size for f in it[0].glob("*.mp4")) / 1024) if it else 0,
                "screenshots": len(list(it[0].rglob("*.png"))) if it else 0,
                "tail": (p.stdout + p.stderr)[-600:] if p.returncode else "",
            }
            print(f"exported {stem} / {eng}: exit {p.returncode} in {took:.0f}s")
    return res


def ms(v):
    return "–" if v is None else f"{v / 1000:.1f}s"


def report(r):
    L = ["# Real-site run: first run vs replay, Playwright vs Selenium", "",
         f"AI: {r['health'].get('llm')} · machines: hub-local", "",
         "| Scenario | Engine | Iter | 1st run | steps | 1st total | locate (sum/median/max) | Replay | replay total | replay locate (sum/median) | healed | video | shots | live frames |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    fails = []
    for stem, runs in r["scenarios"].items():
        for x in runs:
            f, p = x["first"], x.get("replay") or {}
            L.append(f"| {stem} | {x['engine']} | {x['iteration'] or '1'} | {f['status']} | {f['passed']}/{f['steps']} | {ms(f['duration_ms'])} | "
                     f"{ms(f['locate_total_ms'])} / {f['locate_median_ms'] or '–'} ms / {f['locate_max_ms'] or '–'} ms | {p.get('status', '–')} | "
                     f"{ms(p.get('duration_ms'))} | {ms(p.get('locate_total_ms'))} / {p.get('locate_median_ms') or '–'} ms | {p.get('healed', '–')} | "
                     f"{f['artifacts']['video_kb']} KB | {f['artifacts']['screenshots']} | {f['live']['frames']} |")
            for phase, y in (("first", f), ("replay", p)):
                if y and y.get("error"):
                    fails.append(f"- **{stem} / {x['engine']} / {x['iteration'] or '1'} / {phase}**: {y['error']}")
    if fails:
        L += ["", "## Failures", ""] + fails
    if r.get("exported"):
        L += ["", "## Exported projects run on their own", "", "| Project | exit | status | steps | time | report | junit | video | shots |", "|---|---|---|---|---|---|---|---|---|"]
        for k, v in r["exported"].items():
            if "skipped" in v or "error" in v:
                L.append(f"| {k} | {v.get('skipped') or v.get('error')} |||||||| ")
            else:
                L.append(f"| {k} | {v['exit']} | {v['status']} | {v['steps']} | {v['seconds']}s | {v['report']} | {v['junit']} | {v['video_kb']} KB | {v['screenshots']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
