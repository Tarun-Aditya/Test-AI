"""Executes a TestCase step by step, resolving locators from intent, and writes artifacts."""
from __future__ import annotations

import json
import logging
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import locators as L
from .data import substitute
from .engines.base import Engine, make_engine
from .models import (ELEMENT_ACTIONS, Action, Locator, RunConfig, RunSummary, Step, StepResult, StepStatus,
                     TestCase)

log = logging.getLogger(__name__)

EventCb = Callable[[dict], None]


class StepError(Exception):
    pass


def new_run_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "run"
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug}-{uuid.uuid4().hex[:6]}"


class Runner:
    def __init__(self, test: TestCase, config: RunConfig, artifacts_root: Path, mode: str = "ai",
                 on_event: EventCb | None = None, agent: str = "local", run_id: str | None = None):
        self.test = test
        self.config = config
        self.mode = mode
        self.on_event = on_event or (lambda e: None)
        self.run_id = run_id or new_run_id(test.name)
        self.run_dir = Path(artifacts_root) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self.agent = agent
        self.engine: Engine | None = None

    # ---------------------------------------------------------------- public
    def run(self) -> RunSummary:
        started = time.time()
        summary = RunSummary(run_id=self.run_id, test_name=self.test.name, engine=self.config.engine,
                             status=StepStatus.PASSED, started_at=_now(), agent=self.agent, mode=self.mode)
        resolved_steps: list[Step] = [s.model_copy(deep=True) for s in self.test.steps]
        self._emit("run_started", run_id=self.run_id, total=len(resolved_steps), test=self.test.name)
        failed = False
        try:
            self.engine = make_engine(self.config, self.run_dir)
            self.engine.start()
            for i, template_step in enumerate(resolved_steps):
                if failed and not template_step.optional:
                    res = StepResult(index=i, step=template_step, status=StepStatus.SKIPPED)
                else:
                    res = self._run_step(i, template_step)
                    if res.status == StepStatus.FAILED and not template_step.optional:
                        failed = True
                summary.results.append(res)
                self._emit("step_finished", index=i, status=res.status.value, error=res.error,
                           screenshot=res.screenshot, raw=template_step.raw, action=template_step.action.value)
        except Exception as e:  # browser failed to start etc.
            failed = True
            log.exception("run crashed")
            summary.results.append(StepResult(index=len(summary.results), step=Step(action=Action.WAIT, raw="<engine>"),
                                              status=StepStatus.FAILED, error=f"{type(e).__name__}: {e}"))
        finally:
            if self.engine:
                try:
                    summary.video = self.engine.stop()
                except Exception as e:
                    log.warning("engine stop failed: %s", e)

        summary.status = StepStatus.FAILED if failed else StepStatus.PASSED
        summary.finished_at = _now()
        summary.duration_ms = int((time.time() - started) * 1000)
        self._write_artifacts(summary, resolved_steps)
        self._emit("run_finished", run_id=self.run_id, status=summary.status.value)
        return summary

    # ---------------------------------------------------------------- steps
    def _run_step(self, i: int, step: Step) -> StepResult:
        """`step` is the template (with ${vars}); it gets the resolved locator written back to it."""
        e = self.engine
        assert e
        t0 = time.time()
        target = substitute(step.target, self.test.data)
        value = substitute(step.value, self.test.data)
        live = step.model_copy(update={"target": target, "value": value})
        self._emit("step_started", index=i, raw=step.raw, action=step.action.value, target=target, value=_mask(value, step))
        res = StepResult(index=i, step=step, status=StepStatus.PASSED)
        try:
            loc = None
            needs_el = step.action in ELEMENT_ACTIONS or (step.target and step.action in (
                Action.PRESS, Action.WAIT, Action.SCROLL, Action.ASSERT_TEXT))
            if needs_el:
                loc, healed = self._resolve(live, step)
                res.healed = healed
                if self.config.screenshot_each_step:
                    e.highlight(loc)
            self._perform(live, loc)
            e.wait_for_load()
        except Exception as ex:
            res.status = StepStatus.FAILED
            res.error = _short_error(ex)
            log.debug(traceback.format_exc())
        res.duration_ms = int((time.time() - t0) * 1000)
        try:
            res.url = e.url()
        except Exception:
            pass
        if self.config.screenshot_each_step or res.status == StepStatus.FAILED:
            shot = self.run_dir / "screenshots" / f"step_{i + 1:02d}.png"
            try:
                e.screenshot(shot)
                res.screenshot = f"screenshots/{shot.name}"
            except Exception:
                pass
        return res

    def _resolve(self, live: Step, template: Step) -> tuple[Locator, bool]:
        """Find the element. Replay mode tries the stored locator + fallbacks first, then self-heals."""
        e = self.engine
        stored = [template.locator] + template.fallbacks if template.locator else []
        if stored:
            per_try = max(1500, self.config.timeout_ms // max(1, len(stored)))
            for idx, loc in enumerate(stored):
                if e.exists(loc, per_try if idx == 0 else 800):
                    if idx > 0:  # promote the working fallback so the regenerated script is already healed
                        template.locator, template.fallbacks = loc, [l for l in stored if l is not loc][:3]
                    return loc, idx > 0
            if not self.config.self_heal:
                raise StepError(f"Element not found with stored locator {template.locator.value!r}")
            self._emit("healing", target=live.target)
        if not live.target:
            raise StepError("Step needs an element but has no target description")

        deadline = time.time() + self.config.timeout_ms / 1000
        ranked: list[dict] = []
        while True:
            candidates = e.snapshot()
            winner, ranked = L.choose(live, candidates, use_llm=self.config.use_llm)
            if winner and winner.get("locators"):
                locs = L.to_locators(winner)
                template.locator, template.fallbacks = locs[0], locs[1:4]
                return locs[0], bool(stored)
            if time.time() > deadline:
                break
            time.sleep(0.5)
        near = "; ".join(f'{L.describe(c)} (score {c["score"]})' for c in ranked[:3])
        raise StepError(f"Could not identify element for '{live.target}'. Closest: {near or 'none'}")

    def _perform(self, s: Step, loc: Locator | None) -> None:
        e = self.engine
        a, v = s.action, s.value
        timeout = self.config.timeout_ms / 1000
        if a == Action.NAVIGATE:
            url = v or self.test.base_url
            if not url:
                raise StepError("No URL to navigate to")
            e.goto(url)
        elif a == Action.BACK:
            e.back()
        elif a == Action.REFRESH:
            e.refresh()
        elif a == Action.CLICK:
            e.click(loc)
        elif a == Action.DOUBLE_CLICK:
            e.click(loc, double=True)
        elif a == Action.RIGHT_CLICK:
            e.click(loc, button="right")
        elif a == Action.TYPE:
            e.fill(loc, v or "", clear=True)
        elif a == Action.APPEND:
            e.fill(loc, v or "", clear=False)
        elif a == Action.PRESS:
            e.press(v or "Enter", loc)
        elif a == Action.HOVER:
            e.hover(loc)
            time.sleep(0.4)  # let menus open for the screenshot
        elif a == Action.SELECT:
            e.select(loc, v or "")
        elif a == Action.CHECK:
            e.set_checked(loc, True)
        elif a == Action.UNCHECK:
            e.set_checked(loc, False)
        elif a == Action.UPLOAD:
            e.upload(loc, v or "")
        elif a == Action.SCROLL:
            if loc:
                e.scroll_into_view(loc)
            else:
                direction, _, amt = (v or "down").partition(":")
                e.scroll(direction, int(amt) if amt.isdigit() else 600)
        elif a == Action.WAIT:
            if not loc:
                time.sleep(float(v or 1))
        elif a == Action.SCREENSHOT:
            name = re.sub(r"[^\w.-]", "_", v or f"capture_{int(time.time())}")
            e.screenshot(self.run_dir / "screenshots" / f"{name}.png", full_page=True)
        elif a == Action.ASSERT_VISIBLE:
            pass  # resolving the locator already proved it exists
        elif a in (Action.ASSERT_TEXT, Action.ASSERT_URL, Action.ASSERT_TITLE):
            expected = (v or "").strip()
            end = time.time() + timeout
            actual = ""
            while True:
                if a == Action.ASSERT_URL:
                    actual = e.url()
                elif a == Action.ASSERT_TITLE:
                    actual = e.title()
                else:
                    actual = e.text_of(loc) if loc else e.page_text()
                if expected.lower() in (actual or "").lower():
                    return
                if time.time() > end:
                    break
                time.sleep(0.4)
            what = {Action.ASSERT_URL: "URL", Action.ASSERT_TITLE: "title"}.get(a, "element text" if loc else "page")
            raise StepError(f"Expected {what} to contain {expected!r}; got {(actual or '')[:200]!r}")
        else:
            raise StepError(f"Unsupported action {a}")

    # ---------------------------------------------------------------- output
    def _write_artifacts(self, summary: RunSummary, resolved_steps: list[Step]) -> None:
        from . import report, scriptgen
        resolved = self.test.model_copy(update={"steps": resolved_steps})
        d = self.run_dir
        (d / "script.json").write_text(resolved.model_dump_json(indent=2))
        (d / "test_playwright.py").write_text(scriptgen.playwright_script(resolved))
        (d / "test_selenium.py").write_text(scriptgen.selenium_script(resolved))
        (d / "data.json").write_text(json.dumps(self.test.data, indent=2))
        summary.artifacts = {
            "report": "report.html", "script": "script.json", "playwright": "test_playwright.py",
            "selenium": "test_selenium.py", "data": "data.json", "summary": "summary.json",
        }
        if summary.video:
            summary.artifacts["video"] = summary.video
        (d / "summary.json").write_text(summary.model_dump_json(indent=2))
        (d / "report.html").write_text(report.render(summary, resolved, d), encoding="utf-8")

    def _emit(self, kind: str, **kw) -> None:
        try:
            self.on_event({"type": kind, "run_id": self.run_id, **kw})
        except Exception:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_error(ex: Exception) -> str:
    msg = str(ex).strip().splitlines()
    first = msg[0] if msg else ""
    return f"{type(ex).__name__}: {first}"[:500]


def _mask(value: str | None, step: Step) -> str | None:
    """Hide secrets in live events (anything typed into a password-like field or ${password} vars)."""
    hint = f"{step.target} {step.raw} {step.value}".lower()
    if value and any(w in hint for w in ("password", "passwd", "secret", "token", "pin")):
        return "••••••"
    return value


def run_test(test: TestCase, config: RunConfig, artifacts_root: str | Path = "artifacts", mode: str = "ai",
             on_event: EventCb | None = None, agent: str = "local", run_id: str | None = None) -> RunSummary:
    return Runner(test, config, Path(artifacts_root), mode=mode, on_event=on_event, agent=agent, run_id=run_id).run()


def load_script(path: str | Path) -> TestCase:
    return TestCase.model_validate_json(Path(path).read_text())
