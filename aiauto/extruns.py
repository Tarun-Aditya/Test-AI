"""Runs executed by the Chrome extension, reported to the hub.

The extension drives the browser; the hub keeps the record, the same record a run in the app leaves: a row in the run list
(so dashboards, stats and the reporting views count it), live events for anyone watching, a screenshot per step, a video
(built from the tab's screencast frames), report.html, summary.json, a replayable script.json (with the locator of every element
the run acted on) and generated Playwright / Selenium scripts. Everything is masked the way an app run is.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .data import Masker, masked_data
from .locators import to_locators
from .models import Action, RunConfig, RunSummary, Step, StepResult, StepStatus, TestCase

log = logging.getLogger("aiauto.extruns")
CANVAS = (1280, 720)


def _now() -> str:
    from .db import now_iso
    return now_iso()


def ffmpeg_exe() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def build_video(frames: list[tuple[int, Path]], end_ms: int, out: Path) -> bool:
    """Screencast frames (time in ms, jpeg) -> one video. Frames arrive only when the page changes, so each is shown until the next."""
    exe = ffmpeg_exe()
    if not exe or not frames:
        return False
    frames = sorted(frames)
    lst = out.parent / "frames.txt"
    lines = []
    for i, (t, p) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else max(end_ms, t + 500)
        lines.append(f"file '{p.as_posix()}'\nduration {max(nxt - t, 40) / 1000:.3f}")
    lines.append(f"file '{frames[-1][1].as_posix()}'")           # the concat demuxer ignores the last duration
    lst.write_text("\n".join(lines), encoding="utf-8")
    w, h = CANVAS
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,fps=10,format=yuv420p"
    try:
        r = subprocess.run([exe, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
                            "-c:v", "libvpx", "-b:v", "900k", "-deadline", "realtime", "-cpu-used", "5", str(out)],
                           capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
        if not ok:
            log.warning("video build failed: %s", r.stderr[-300:])
        return ok
    except Exception as e:
        log.warning("video build failed: %s", e)
        return False
    finally:
        lst.unlink(missing_ok=True)


def _save_image(data_url: str, dest_noext: Path) -> str | None:
    """A data: URL (or bare base64) -> a file. Returns the file name."""
    if not data_url:
        return None
    ext = "jpg"
    b64 = data_url
    if data_url.startswith("data:"):
        head, _, b64 = data_url.partition(",")
        ext = "png" if "png" in head else "jpg"
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    p = dest_noext.with_suffix("." + ext)
    p.write_bytes(raw)
    return p.name


class ExtRuns:
    def __init__(self, artifacts: Path):
        self.root = Path(artifacts)
        self.state: dict[str, dict[str, Any]] = {}

    def create(self, run_id: str, test: TestCase, mode: str = "ai") -> Path:
        d = self.root / run_id
        (d / "screenshots").mkdir(parents=True, exist_ok=True)
        self.state[run_id] = {"test": test, "dir": d, "mask": Masker(test.data, test.sensitive), "results": {}, "frames": [],
                              "dialog": {}, "cur": None, "t0": time.time(), "tabs": [], "started_at": _now(), "mode": mode}
        return d

    def known(self, run_id: str) -> bool:
        return run_id in self.state

    # ---------------------------------------------------------------------------------------- events
    def handle(self, run_id: str, ev: dict) -> list[dict]:
        """One event from the extension -> the events the hub broadcasts (and stores) for it."""
        st = self.state.get(run_id)
        if not st:
            return []
        test: TestCase = st["test"]
        mask: Masker = st["mask"]
        t = ev.get("type")
        if t == "run_started":
            return [{"type": "run_started", "run_id": run_id, "total": len(test.steps), "test": test.title or test.name,
                     "engine": "extension", "agent": "Chrome extension"}]
        if t == "step_started":
            i = int(ev.get("index", 0))
            st["cur"] = i
            s = test.steps[i] if 0 <= i < len(test.steps) else None
            return [{"type": "step_started", "run_id": run_id, "index": i, "raw": mask(ev.get("raw") or (s and s.raw) or ""),
                     "action": ev.get("action") or (s and s.action.value), "target": mask(s.target) if s and s.target else None,
                     "value": ev.get("value"), "origin": mask(s.origin) if s and s.origin else None,
                     "parsed_by": s.source if s else None}]
        if t == "dialog":
            if st["cur"] is not None:
                st["dialog"][st["cur"]] = mask(ev.get("message") or "")
            return []
        if t == "tab":
            st["tabs"].append({"index": ev.get("index"), "tab": ev.get("tab")})
            return []
        if t == "step_finished":
            i = int(ev.get("index", 0))
            if not 0 <= i < len(test.steps):
                return []
            step = test.steps[i].model_copy(deep=True)
            el = ev.get("el")
            if el and el.get("tag") and ev.get("picked") != "recorded":
                try:
                    locs = to_locators(el)
                    if locs:
                        step.locator, step.fallbacks = locs[0], locs[1:6]
                except Exception:
                    pass
            shot = _save_image(ev.get("shot") or "", st["dir"] / "screenshots" / f"step-{i + 1:02d}")
            rel = f"screenshots/{shot}" if shot else None
            status = StepStatus(ev.get("status") if ev.get("status") in ("passed", "failed", "skipped") else "failed")
            res = StepResult(index=i, step=step, status=status, duration_ms=int(ev.get("ms") or 0), screenshot=rel,
                             error=mask(ev.get("error")) if ev.get("error") else None, picked_by=ev.get("picked"),
                             url=mask(ev.get("url") or ""), dialog=st["dialog"].get(i), shown_value=mask(ev.get("value")) if ev.get("value") else None)
            st["results"][i] = res
            return [{"type": "step_finished", "run_id": run_id, "index": i, "status": status.value, "error": res.error, "healed": False,
                     "screenshot": rel, "raw": mask(step.raw), "action": step.action.value,
                     "locator": step.locator.label() if step.locator else None, "parsed_by": step.source, "picked_by": res.picked_by,
                     "origin": mask(step.origin) if step.origin else None, "url": res.url, "duration_ms": res.duration_ms, "dialog": res.dialog}]
        return []

    def add_frames(self, run_id: str, frames: list[dict]) -> dict | None:
        """Screencast frames -> files. Returns the newest frame as a live_frame event."""
        st = self.state.get(run_id)
        if not st or not frames:
            return None
        d = st["dir"] / "frames"
        d.mkdir(exist_ok=True)
        last = None
        for f in frames:
            try:
                raw = base64.b64decode(f["data"])
            except Exception:
                continue
            p = d / f"{len(st['frames']):06d}.jpg"
            p.write_bytes(raw)
            st["frames"].append((int(f.get("t", 0)), p))
            last = f["data"]
        return {"type": "live_frame", "run_id": run_id, "mime": "image/jpeg", "image": last} if last else None

    # ---------------------------------------------------------------------------------------- finish
    def finish(self, run_id: str, ev: dict) -> dict:
        """Write everything a run leaves behind. Returns {status, error}."""
        st = self.state.get(run_id)
        if not st:
            return {"status": ev.get("status", "failed"), "error": ev.get("error")}
        test: TestCase = st["test"]
        mask: Masker = st["mask"]
        d: Path = st["dir"]
        status = {"passed": StepStatus.PASSED, "cancelled": StepStatus.CANCELLED}.get(ev.get("status"), StepStatus.FAILED)
        results = []
        for i, s in enumerate(test.steps):
            results.append(st["results"].get(i) or StepResult(index=i, step=s, status=StepStatus.SKIPPED))
        if ev.get("error") and status == StepStatus.FAILED and not any(r.status == StepStatus.FAILED for r in results):
            results.append(StepResult(index=len(results), step=Step(action="wait", raw="<extension>"), status=StepStatus.FAILED,
                                      error=mask(str(ev["error"]))[:800]))
        end_ms = int((time.time() - st["t0"]) * 1000)
        video = None
        base = st["frames"][0][0] if st["frames"] else 0
        frames = [(t - base, p) for t, p in st["frames"]]
        if frames and build_video(frames, max(int(ev.get("ended_ms", 0)) - base, frames[-1][0] + 500), d / "video.webm"):
            video = "video.webm"
        shutil.rmtree(d / "frames", ignore_errors=True)
        summary = RunSummary(run_id=run_id, test_name=test.title or test.name, engine="extension", status=status,
                             started_at=st.get("started_at") or _now(), finished_at=_now(), duration_ms=end_ms, results=results,
                             video=video, agent="Chrome extension", mode=st.get("mode", "ai"))
        self._write(d, summary, test, mask)
        self.state.pop(run_id, None)
        return {"status": status.value, "error": mask(ev["error"]) if ev.get("error") else None}

    def _write(self, d: Path, summary: RunSummary, test: TestCase, mask: Masker) -> None:
        from . import report, scriptgen
        safe = masked_data(test.data, test.sensitive)
        steps = [r.step for r in summary.results if r.step.raw != "<extension>"]
        if test.base_url and (not steps or steps[0].action != Action.NAVIGATE):
            # the run started on a page that was already open: a replay (in the app, or in the extension on another tab) opens it first
            steps = [Step(action=Action.NAVIGATE, value=test.base_url, raw=f"open {test.base_url}", source="rules")] + steps
        resolved = test.model_copy(update={"steps": steps, "data": safe, "engine": None})
        (d / "script.json").write_text(resolved.model_dump_json(indent=2), encoding="utf-8")
        cfg = RunConfig().model_dump()
        for name, fn in (("test_playwright.py", scriptgen.playwright_script), ("test_selenium.py", scriptgen.selenium_script)):
            try:
                (d / name).write_text(fn(resolved, cfg), encoding="utf-8")
            except Exception as e:
                log.warning("could not generate %s: %s", name, e)
        (d / "autopilot_runtime.py").write_text(Path(__file__).with_name("script_runtime.py").read_text(), encoding="utf-8")
        (d / "data.json").write_text(json.dumps(safe, indent=2), encoding="utf-8")
        summary.artifacts = {"report": "report.html", "script": "script.json", "playwright": "test_playwright.py",
                             "selenium": "test_selenium.py", "data": "data.json", "summary": "summary.json"}
        if summary.video:
            summary.artifacts["video"] = summary.video
        (d / "summary.json").write_text(json.dumps(mask.deep(summary.model_dump(mode="json")), indent=2), encoding="utf-8")
        (d / "report.html").write_text(report.render(summary, resolved, d, mask), encoding="utf-8")
