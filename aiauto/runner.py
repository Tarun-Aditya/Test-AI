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
from .data import MASK, Masker, masked_data, substitute
from .engines.base import Engine, make_engine
from .models import (ELEMENT_ACTIONS, Action, Locator, RunConfig, RunSummary, Step, StepResult, StepStatus,
                     TestCase)

log = logging.getLogger(__name__)

EventCb = Callable[[dict], None]
_UNRESOLVED = re.compile(r"\$\{[^}]+\}|\{\{[^}]+\}\}")


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
        self._listening = on_event is not None
        self.run_id = run_id or new_run_id(test.name)
        self.run_dir = Path(artifacts_root) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self.agent = agent
        self.engine: Engine | None = None
        self.mask = Masker(test.data, test.sensitive)
        self.saved_session: dict | None = None

    # ---------------------------------------------------------------- public
    def run(self) -> RunSummary:
        started = time.time()
        summary = RunSummary(run_id=self.run_id, test_name=self.test.name, engine=self.config.engine,
                             status=StepStatus.PASSED, started_at=_now(), agent=self.agent, mode=self.mode)
        resolved_steps: list[Step] = [s.model_copy(deep=True) for s in self.test.steps]
        self._emit("run_started", total=len(resolved_steps), test=self.test.name, engine=self.config.engine)
        failed = False
        try:
            self.engine = make_engine(self.config, self.run_dir)
            if self._listening:
                self.engine.on_live = lambda mime, b64: self._emit("live_frame", mime=mime, image=b64)
            self.engine.start()
            self.engine.live_tick(force=True)
            for i, template_step in enumerate(resolved_steps):
                if failed and not template_step.optional:
                    res = StepResult(index=i, step=template_step, status=StepStatus.SKIPPED)
                else:
                    res = self._run_step(i, template_step)
                    if res.status == StepStatus.FAILED and not template_step.optional:
                        failed = True
                summary.results.append(res)
                self._emit("step_finished", index=i, status=res.status.value, error=res.error, healed=res.healed,
                           screenshot=res.screenshot, raw=template_step.raw, action=template_step.action.value,
                           locator=template_step.locator.label() if template_step.locator else None,
                           url=res.url, duration_ms=res.duration_ms, dialog=res.dialog)
            if self.config.save_session and not failed:
                self.saved_session = self.engine.storage_state()
                summary.session_saved = self.config.save_session if self.saved_session else None
        except Exception as e:  # browser failed to start etc.
            failed = True
            log.exception("run crashed")
            summary.results.append(StepResult(index=len(summary.results), step=Step(action=Action.WAIT, raw="<engine>"),
                                              status=StepStatus.FAILED, error=self.mask(f"{type(e).__name__}: {e}")[:800]))
        finally:
            if self.engine:
                try:
                    summary.video = self.engine.stop()
                except Exception as e:
                    log.warning("engine stop failed: %s", e)

        summary.status = StepStatus.FAILED if failed else StepStatus.PASSED
        summary.finished_at = _now()
        summary.duration_ms = int((time.time() - started) * 1000)
        summary.healed_steps = sum(1 for r in summary.results if r.healed)
        self._write_artifacts(summary, resolved_steps)
        self._emit("run_finished", status=summary.status.value, healed=summary.healed_steps)
        return summary

    # ---------------------------------------------------------------- steps
    def _run_step(self, i: int, step: Step) -> StepResult:
        """`step` is the template (with ${vars}); the verified locator is written back to it."""
        e = self.engine
        assert e
        t0 = time.time()
        data = self.test.data
        target = substitute(step.target, data)
        value = substitute(step.value, data)
        secret = self.mask.is_sensitive_template(step.value) or (value and value in self.mask.values)
        shown = MASK if secret and value else self.mask(value)
        live = step.model_copy(update={"target": target, "value": value})
        self._emit("step_started", index=i, raw=self.mask(step.raw), action=step.action.value,
                   target=self.mask(target), value=shown)
        res = StepResult(index=i, step=step, status=StepStatus.PASSED, shown_value=shown)
        e.last_dialog = None
        try:
            for field, v in (("value", value), ("target", target)):
                if v and _UNRESOLVED.search(v):
                    raise StepError(f"No test data for {', '.join(_UNRESOLVED.findall(v))} (in step {field})")
            loc = None
            needs_el = step.action in ELEMENT_ACTIONS or (step.target and step.action in (
                Action.PRESS, Action.WAIT, Action.SCROLL, Action.ASSERT_TEXT))
            if needs_el:
                loc, healed = self._resolve(live, step, target)
                res.healed = healed
                if self.config.screenshot_each_step:
                    e.highlight(loc)
                if secret and step.action in (Action.TYPE, Action.APPEND):
                    e.mask(loc)
            self._perform(live, step, loc)
            e.after_action()
            if e.last_dialog:
                self._prev_dialog = e.last_dialog
            e.wait_for_load()
        except Exception as ex:
            res.status = StepStatus.SKIPPED if step.optional else StepStatus.FAILED
            res.error = ("optional step skipped: " if step.optional else "") + self.mask(_short_error(ex))
            log.debug(traceback.format_exc())
            try:
                e.after_action()
            except Exception:
                pass
        res.dialog = self.mask(e.last_dialog)
        res.duration_ms = int((time.time() - t0) * 1000)
        try:
            res.url = self.mask(e.url())
        except Exception:
            pass
        if self.config.screenshot_each_step or res.status == StepStatus.FAILED:
            shot = self.run_dir / "screenshots" / f"step_{i + 1:02d}.png"
            try:
                e.screenshot(shot, full_page=self.config.full_page_screenshots)
                res.screenshot = f"screenshots/{shot.name}"
                if self.config.live_view and self._listening:
                    import base64
                    self._emit("step_shot", index=i, mime="image/png", image=base64.b64encode(shot.read_bytes()).decode())
            except Exception:
                pass
        return res

    def _resolve(self, live: Step, template: Step, target: str | None, attr: str = "locator") -> tuple[Locator, bool]:
        """Find the element.
        Replay: stored locator -> stored fallbacks -> (self-heal) AI re-identification from the description.
        AI mode: snapshot + intent scoring, then verify candidate locators natively in the engine."""
        e = self.engine
        stored_primary: Locator | None = getattr(template, attr)
        stored = ([stored_primary] + (template.fallbacks if attr == "locator" else [])) if stored_primary else []
        if template.nth is not None and attr == "locator":
            stored = []          # positional picks ("first result containing X") depend on live data: always re-pick
        if stored:
            for idx, loc in enumerate(stored):
                if e.exists(loc, max(1500, self.config.timeout_ms // 2) if idx == 0 else 700) and \
                        (e.count(loc) == 1 or loc.strategy in ("css", "xpath")):
                    if idx > 0 and attr == "locator":
                        # promote the working fallback so the regenerated script is already healed
                        template.locator, template.fallbacks = loc, [l for l in stored if l is not loc][:3]
                    if idx > 0:
                        self._emit("healing", target=self.mask(target), how=f"fallback locator {loc.label()}")
                    return loc, idx > 0
            if not self.config.self_heal:
                raise StepError(f"Element not found with stored locator {stored_primary.label()} (self-heal off)")
            self._emit("healing", target=self.mask(target), how="re-identifying from description")
        if not target:
            raise StepError("Step needs an element but has no target description")

        probe = live.model_copy(update={"action": Action.CLICK}) if attr == "target_locator" else live
        wait_ms = min(self.config.timeout_ms, 4000) if template.optional else self.config.timeout_ms
        deadline = time.time() + wait_ms / 1000
        ranked: list[dict] = []
        while True:
            e.after_action()
            try:
                candidates = e.snapshot()
            except Exception as ex:
                log.debug("snapshot failed: %s", ex)
                candidates = []
            winner, ranked = L.choose(probe, candidates, target=target, use_llm=self.config.use_llm)
            if winner:
                verified = self._verify(winner)
                if verified:
                    if attr == "locator":
                        template.locator, template.fallbacks = verified[0], verified[1:4]
                    else:
                        template.target_locator = verified[0]
                    return verified[0], bool(stored)
            if time.time() > deadline:
                break
            e.live_tick()
            time.sleep(0.5)
        near = "; ".join(f'{L.describe(c)} (score {c.get("score")})' for c in ranked[:3])
        raise StepError(self.mask(f"Could not identify element for '{target}'. Closest: {near or 'none'}"))

    def _verify(self, cand: dict) -> list[Locator]:
        """Keep only locators that the engine itself resolves to exactly this one element."""
        e = self.engine
        good: list[Locator] = []
        all_locs = L.to_locators(cand)
        for loc in all_locs:
            try:
                if e.count(loc) != 1:
                    continue
                m = e.marker(loc)
                if m is not None and m != cand.get("marker"):
                    continue
                good.append(loc)
            except Exception as ex:
                log.debug("verify %s failed: %s", loc.label(), ex)
            if len(good) >= 4:
                break
        if not good:
            good = [l for l in all_locs if l.strategy in ("css", "xpath")][:1]
        return good

    def _perform(self, s: Step, template: Step, loc: Locator | None) -> None:
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
        elif a == Action.SWITCH_TAB:
            e.switch_tab(v or "last")
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
            options = [o.strip() for o in (v or "").split("|") if o.strip()]
            if e.info(loc)["tag"] == "select":
                e.select(loc, options)
            else:   # custom dropdown: open it, then click each option by its text
                for opt in options:
                    e.click(loc)
                    time.sleep(0.3)
                    opt_step = Step(action=Action.CLICK, target=opt, raw=f"option {opt}")
                    oloc, _ = self._resolve(opt_step, opt_step, opt)
                    e.click(oloc)
        elif a == Action.CHECK:
            e.set_checked(loc, True)
        elif a == Action.UNCHECK:
            e.set_checked(loc, False)
        elif a == Action.UPLOAD:
            e.upload(loc, v or "")
        elif a == Action.DRAG:
            if not v:
                raise StepError("Drag needs a destination")
            dst, _ = self._resolve(s, template, v, attr="target_locator")
            e.drag(loc, dst)
        elif a == Action.SCROLL:
            if loc:
                e.scroll_into_view(loc)
            else:
                direction, _, amt = (v or "down").partition(":")
                e.scroll(direction, int(amt) if amt.isdigit() else 600)
        elif a == Action.WAIT:
            if not loc:
                end = time.time() + float(v or 1)
                while time.time() < end:
                    e.live_tick()
                    time.sleep(min(0.3, max(0.0, end - time.time())))
        elif a == Action.PAUSE:
            secs = float(v or 30)
            self._emit("paused", seconds=secs, message="Complete the manual step (e.g. SSO / MFA login) in the browser")
            end = time.time() + secs
            while time.time() < end:
                time.sleep(0.5)
                e.frame()
                e.live_tick()
        elif a == Action.DIALOG:
            e.dialog_policy = "dismiss" if (v or "").lower().startswith(("dismiss", "cancel", "reject")) else "accept"
        elif a == Action.SCREENSHOT:
            name = re.sub(r"[^\w.-]", "_", v or f"capture_{int(time.time())}")
            e.screenshot(self.run_dir / "screenshots" / f"{name}.png", full_page=True)
        elif a == Action.ASSERT_VISIBLE:
            if not e.is_visible(loc):
                raise StepError(f"'{s.target}' exists but is not visible")
        elif a in (Action.ASSERT_TEXT, Action.ASSERT_URL, Action.ASSERT_TITLE, Action.ASSERT_DIALOG):
            expected = (v or "").strip()
            end = time.time() + timeout
            actual = ""
            while True:
                if a == Action.ASSERT_URL:
                    actual = e.url()
                elif a == Action.ASSERT_TITLE:
                    actual = e.title()
                elif a == Action.ASSERT_DIALOG:
                    actual = e.last_dialog or getattr(self, "_prev_dialog", "") or ""
                else:
                    actual = e.text_of(loc) if loc else e.page_text()
                if _norm(expected) in _norm(actual):
                    return
                if time.time() > end:
                    break
                e.live_tick()
                time.sleep(0.4)
            what = {Action.ASSERT_URL: "URL", Action.ASSERT_TITLE: "title", Action.ASSERT_DIALOG: "dialog"}.get(
                a, "element text" if loc else "page")
            raise StepError(f"Expected {what} to contain {expected!r}; got {_excerpt(actual, expected)!r}")
        else:
            raise StepError(f"Unsupported action {a}")
        if e.last_dialog:
            self._prev_dialog = e.last_dialog

    # ---------------------------------------------------------------- output
    def _write_artifacts(self, summary: RunSummary, resolved_steps: list[Step]) -> None:
        from . import report, scriptgen
        safe_data = masked_data(self.test.data, self.test.sensitive)
        resolved = self.test.model_copy(update={"steps": resolved_steps, "data": safe_data, "engine": self.config.engine})
        d = self.run_dir
        (d / "script.json").write_text(resolved.model_dump_json(indent=2))
        (d / "test_playwright.py").write_text(scriptgen.playwright_script(resolved))
        (d / "test_selenium.py").write_text(scriptgen.selenium_script(resolved))
        (d / "data.json").write_text(json.dumps(safe_data, indent=2))
        summary.artifacts = {
            "report": "report.html", "script": "script.json", "playwright": "test_playwright.py",
            "selenium": "test_selenium.py", "data": "data.json", "summary": "summary.json",
        }
        if summary.video:
            summary.artifacts["video"] = summary.video
        summary_json = summary.model_dump_json(indent=2)
        (d / "summary.json").write_text(self.mask(summary_json))
        (d / "report.html").write_text(report.render(summary, resolved, d, self.mask), encoding="utf-8")

    def _emit(self, kind: str, **kw) -> None:
        try:
            self.on_event({"type": kind, "run_id": self.run_id, **kw})
        except Exception:
            pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _excerpt(actual: str, expected: str) -> str:
    a = re.sub(r"\s+", " ", actual or "").strip()
    return a[:300] + ("…" if len(a) > 300 else "")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_error(ex: Exception) -> str:
    msg = str(ex).strip().splitlines()
    first = msg[0] if msg else ""
    return f"{type(ex).__name__}: {first}"[:600]


def run_test(test: TestCase, config: RunConfig, artifacts_root: str | Path = "artifacts", mode: str = "ai",
             on_event: EventCb | None = None, agent: str = "local", run_id: str | None = None,
             runner_out: list | None = None) -> RunSummary:
    r = Runner(test, config, Path(artifacts_root), mode=mode, on_event=on_event, agent=agent, run_id=run_id)
    if runner_out is not None:
        runner_out.append(r)
    return r.run()


def load_script(path: str | Path) -> TestCase:
    return TestCase.model_validate_json(Path(path).read_text())
