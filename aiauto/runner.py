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
        self._picked: str | None = None
        self.timings: dict[str, list[float]] = {}

    # ---------------------------------------------------------------- public
    def run(self) -> RunSummary:
        started = time.time()
        summary = RunSummary(run_id=self.run_id, test_name=self.test.title or self.test.name, engine=self.config.engine,
                             status=StepStatus.PASSED, started_at=_now(), agent=self.agent, mode=self.mode)
        resolved_steps: list[Step] = [s.model_copy(deep=True) for s in self.test.steps]
        self._emit("run_started", total=len(resolved_steps), test=self.test.title or self.test.name, engine=self.config.engine)
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
                           parsed_by=template_step.source, picked_by=res.picked_by, origin=self.mask(template_step.origin),
                           url=res.url, duration_ms=res.duration_ms, dialog=res.dialog, locate_ms=res.locate_ms)
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
        summary.timings = {k: {"count": len(v), "avg_ms": round(sum(v) / len(v), 1), "max_ms": round(max(v), 1),
                               "total_ms": round(sum(v))} for k, v in self.timings.items() if v}
        located = [r.locate_ms for r in summary.results if r.locate_ms is not None]
        if located:
            summary.timings["locate_step"] = {"count": len(located), "avg_ms": round(sum(located) / len(located), 1),
                                              "max_ms": max(located), "total_ms": sum(located)}
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
        self._picked = None
        self._emit("step_started", index=i, raw=self.mask(step.raw), action=step.action.value,
                   origin=self.mask(step.origin), parsed_by=step.source,
                   target=self.mask(target), value=shown)
        res = StepResult(index=i, step=step, status=StepStatus.PASSED, shown_value=shown)
        e.last_dialog = None
        loc = None
        verb, done = _VERBS.get(step.action, (step.action.value.replace("_", " "), "done"))
        try:
            for field, v in (("value", value), ("target", target)):
                if v and _UNRESOLVED.search(v):
                    raise StepError(f"No test data for {', '.join(_UNRESOLVED.findall(v))} (in step {field})")
            needs_el = step.action in ELEMENT_ACTIONS or (step.target and step.action in (
                Action.PRESS, Action.WAIT, Action.SCROLL, Action.ASSERT_TEXT))
            if needs_el:
                t_loc = time.perf_counter()
                loc, healed = self._resolve(live, step, target)
                res.locate_ms = int((time.perf_counter() - t_loc) * 1000)
                res.healed = healed
                res.picked_by = self._picked
                # dashed green box: "this is the element", shown briefly before acting (video / live view)
                if e.show_box(loc, "located", f"Step {i + 1} · {verb}") and self.config.highlight_ms and \
                        (self.config.record_video or (self.config.live_view and self._listening)):
                    time.sleep(self.config.highlight_ms / 1000)
                if secret and step.action in (Action.TYPE, Action.APPEND):
                    e.mask(loc)
            self._perform(live, step, loc)
            e.after_action()
            if e.last_dialog:
                self._prev_dialog = e.last_dialog
            e.wait_for_load()
            # solid green box: action done (stays for the step's screenshot; skipped if the element is gone)
            e.show_box(loc, "done", f"✓ Step {i + 1} · {done}", timeout_ms=800)
        except Exception as ex:
            e.show_box(loc, "failed", f"✗ Step {i + 1} · {verb} failed", timeout_ms=800)
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
        if loc is not None:
            e.clear_box(loc)       # never left on the page for the next step
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
        if stored and stored_primary.strategy == "point":
            # visually located last time: look again (the layout may have moved); stored position without AI
            again = self._visual(live, template, attr, stored) if self._ai() else None
            if again:
                return again[0], False
            self._picked = "script"
            return stored_primary, False
        if stored:
            fits = lambda loc, idx: self._fits(loc, idx, live, target)
            # locators this engine can't run (a Playwright-generated one on Selenium) are skipped, not "healed"
            usable0 = next((i for i, l in enumerate(stored) if not (e.name == "selenium" and l.strategy == "playwright")), 0)
            for idx, loc in enumerate(stored):
                if e.exists(loc, 1200 if idx == 0 else 300) and \
                        (e.count(loc) == 1 or loc.strategy in ("css", "xpath")) and fits(loc, idx):
                    if idx > usable0 and attr == "locator":
                        # promote the working fallback so the regenerated script is already healed
                        template.locator, template.fallbacks = loc, [l for l in stored if l is not loc][:3]
                    if idx > usable0:
                        self._emit("healing", target=self.mask(target), how=f"fallback locator {loc.label()}")
                    self._picked = "script" if idx <= usable0 else "fallback"
                    return loc, idx > usable0
            # not rendered yet (virtualised list, lazy section)? scroll and look for the stored locators
            found = self._scroll_search(lambda: next((l for i, l in enumerate(stored) if e.count(l) >= 1 and fits(l, i)), None),
                                        number=_number_in(target)) if not template.optional else None
            if not found and e.exists(stored_primary, max(1500, self.config.timeout_ms // 2)):
                found = stored_primary          # it was just slow to appear
            if found:
                healed = stored.index(found) > usable0
                self._picked = "fallback" if healed else "script"
                return found, healed
            if not self.config.self_heal:
                raise StepError(f"Element not found with stored locator {stored_primary.label()} (self-heal off)")
            self._emit("healing", target=self.mask(target), how="re-identifying from description")
        if not target:
            raise StepError("Step needs an element but has no target description")

        probe = live.model_copy(update={"action": Action.CLICK}) if attr == "target_locator" else live
        wait_ms = min(self.config.timeout_ms, 4000) if template.optional else self.config.timeout_ms
        deadline = time.time() + wait_ms / 1000
        ranked: list[dict] = []
        problem = None
        scrolled = False
        # sources, best first: the engine's snapshot (Playwright: its own ARIA snapshot), then the DOM walk
        # (reaches elements without an accessible role, e.g. plain-text <div>s with click handlers)
        sources = [e.snapshot] + ([e.snapshot_dom] if getattr(e, "has_dom_fallback", False) else [])
        while True:
            e.after_action()
            for source in sources:
                try:
                    t0 = time.perf_counter()
                    candidates = source(None if probe.nth is not None else L.step_words(target))
                    self._timing("snapshot", t0)
                except Exception as ex:
                    log.debug("snapshot failed: %s", ex)
                    candidates = []
                t0 = time.perf_counter()
                winner, ranked, problem = L.choose(probe, candidates, target=target, use_llm=self.config.use_llm and not hasattr(e, "refine"),
                                                   on_ambiguous=self.config.on_ambiguous)
                if hasattr(e, "refine") and ranked and probe.nth is None:
                    # Selenium/Chrome: real accessible names for the front-runners, then decide among them
                    top = [c for c in candidates if c["uid"] in {r["uid"] for r in ranked[:12]}]
                    if e.refine(top):
                        winner, ranked, problem = L.choose(probe, top, target=target, use_llm=self.config.use_llm,
                                                           on_ambiguous=self.config.on_ambiguous)
                self._timing("rank", t0)
                if winner:
                    t0 = time.perf_counter()
                    verified = self._verify(winner, target)
                    self._timing("verify", t0)
                    if verified:
                        self._picked = winner.get("picked_by") or "heuristic"
                        if attr == "locator":
                            template.locator, template.fallbacks = verified[0], verified[1:4]
                        else:
                            template.target_locator = verified[0]
                        return verified[0], bool(stored)
                if problem and "match equally well" in problem:
                    break                    # a real tie: don't let a weaker source pick one of them silently
            if problem and "match equally well" in problem:
                raise StepError(self.mask(f"Ambiguous '{target}': {problem}"))
            if not scrolled and not template.optional:
                scrolled = True
                # not rendered yet (virtualised list, lazy-loaded section)? scroll step by step and search again
                hit = self._scroll_search(lambda: self._pick(sources, probe, target, attr, template, stored),
                                          number=_number_in(target))
                if hit:
                    return hit
            if time.time() > deadline:
                break
            e.live_tick()
            time.sleep(0.5)
        vis = self._visual(live, template, attr, stored)
        if vis:
            return vis
        near = "; ".join(f'{L.describe(c)} (score {c.get("score")})' for c in ranked[:3])
        hint = ""
        try:
            if e.eval_js("return Array.from(document.querySelectorAll('canvas')).some(c => c.width * c.height > 40000)"):
                hint = (" This page draws content on a <canvas>, which has no DOM elements to locate."
                        + (" The visual fallback found nothing either." if self.config.visual_fallback else "")
                        + ("" if self._ai() else " Set ANTHROPIC_API_KEY to let Claude locate it on a screenshot."))
        except Exception:
            pass
        raise StepError(self.mask(f"Could not identify element for '{target}'. Closest: {near or 'none'}.{hint}"))

    POINT_ACTIONS = (Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK, Action.HOVER, Action.TYPE, Action.APPEND)

    def _ai(self) -> bool:
        from .nlp import llm
        return self.config.use_llm and llm.available()

    def _visual(self, live: Step, template: Step, attr: str, stored: list) -> tuple[Locator, bool] | None:
        """Last resort for elements that exist only as pixels (canvas, images, plugins): Claude looks at a
        screenshot and returns where the element is; the step then acts at that position."""
        if not (self.config.visual_fallback and self._ai() and live.action in self.POINT_ACTIONS and attr == "locator"):
            return None
        from .nlp import llm
        e = self.engine
        shot = self.run_dir / "_visual.png"
        try:
            e.screenshot(shot)
            png = shot.read_bytes()
            img_w, img_h = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
            vw, vh = e.viewport()
        except Exception as ex:
            log.debug("visual fallback screenshot failed: %s", ex)
            return None
        finally:
            shot.unlink(missing_ok=True)
        out = llm.locate_visual(png, live, img_w, img_h)
        if not out:
            return None
        x, y = round(out["x"] * vw / img_w), round(out["y"] * vh / img_h)        # screenshot px -> CSS px
        loc = Locator(strategy="point", value=f"{x},{y}", picked_by="ai-vision",
                      description=f"screen position chosen by Claude from a screenshot: {out.get('reason', '')[:120]}")
        template.locator, template.fallbacks = loc, []
        self._picked = "ai-vision"
        self._emit("healing", target=self.mask(live.target), how=f"located visually at {x},{y}")
        return loc, bool(stored)

    def _fits(self, loc: Locator, idx: int, step: Step, target: str | None) -> bool:
        """Replay safety: a fallback (or a structural CSS/XPath path) must still point at an element matching the
        step's description (numbers included), otherwise e.g. 'second row's button' would click the wrong row
        of a re-rendered list. The primary, name-bearing locator is trusted as is."""
        if not target or (idx == 0 and loc.strategy not in ("css", "xpath")):
            return True
        try:
            info = self.engine.run_js_on(loc, L.INFO_EL_JS)
        except Exception:
            return False
        cand = {**info, "visible": True}
        specific = any(w not in L.STOP for w in L._tokens(target))
        ok = L.score(step, cand, target) >= 1.0 and (not specific or cand.get("_text_match", 0) > 0.25)
        if not ok:
            log.debug("fallback %s rejected: points at %s", loc.label(), L.describe(info) if info else None)
        return ok

    def _pick(self, sources, probe, target, attr, template, stored):
        """One identification pass (used while scroll-searching): returns (locator, healed) or None."""
        e = self.engine
        for source in sources:
            try:
                cands = source(None if probe.nth is not None else L.step_words(target))
            except Exception:
                continue
            winner, ranked, problem = L.choose(probe, cands, target=target, use_llm=self.config.use_llm,
                                               on_ambiguous=self.config.on_ambiguous)
            if problem and "match equally well" in problem:
                raise StepError(self.mask(f"Ambiguous '{target}': {problem}"))
            if winner:
                verified = self._verify(winner, target)
                if verified:
                    self._picked = winner.get("picked_by") or "heuristic"
                    if attr == "locator":
                        template.locator, template.fallbacks = verified[0], verified[1:4]
                    else:
                        template.target_locator = verified[0]
                    return verified[0], bool(stored)
        return None

    def _scroll_search(self, probe_fn, number: int | None = None, budget_s: float | None = None):
        """Find content that isn't rendered yet. 1) scroll the page down step by step (lazy sections);
        2) if the step names a number, binary-search the largest scrollable container by the numbers it shows
        (ordered virtualised lists); 3) scan containers a screen at a time. probe_fn() is called after each
        move; its first truthy result is returned. The page goes back to the top if nothing is found."""
        from .engines.base import BOX_JS, BOX_NUMS_JS, BOX_SEEK_JS, SCROLL_MORE_JS
        e = self.engine
        t0 = time.perf_counter()
        end = time.time() + (budget_s or max(20.0, self.config.timeout_ms / 1000 * 3))
        settle = lambda: (time.sleep(0.12), e.live_tick())
        try:
            # 1) the page itself
            for _ in range(60):
                if time.time() > end or e.eval_js(SCROLL_MORE_JS) != "page":
                    break
                settle()
                res = probe_fn()
                if res:
                    return res
            box = e.eval_js(BOX_JS)
            # 2) ordered list + a number in the step: binary search
            if box and number is not None:
                lo, hi = 0, box["max"]
                for _ in range(24):
                    mid = (lo + hi) // 2
                    e.eval_js(f"return (function(){{ {BOX_SEEK_JS} }}).apply(null, [{mid}])")
                    settle()
                    nums = [n for n in (e.eval_js(BOX_NUMS_JS) or []) if n > 0]
                    if not nums:
                        break
                    if number < min(nums) and hi - lo > 1:
                        hi = mid
                    elif number > max(nums) and hi - lo > 1:
                        lo = mid
                    else:
                        res = probe_fn()
                        if res:
                            return res
                        break
            # 3) containers, a screen at a time
            if box:
                e.eval_js("return (function(){ " + BOX_SEEK_JS + " }).apply(null, [0])")
                pos = 0
                while time.time() < end and pos < box["max"]:
                    pos += box["step"]
                    e.eval_js(f"return (function(){{ {BOX_SEEK_JS} }}).apply(null, [{pos}])")
                    settle()
                    res = probe_fn()
                    if res:
                        return res
            e.eval_js("window.scrollTo(0, 0); return true;")
        except StepError:
            raise
        except Exception as ex:
            log.debug("scroll search failed: %s", ex)
        finally:
            self._timing("scroll_search", t0)
        return None

    def _timing(self, what: str, t0: float) -> None:
        self.timings.setdefault(what, []).append((time.perf_counter() - t0) * 1000)

    @staticmethod
    def _count_tolerant(locs: list[Locator], target: str) -> list[Locator]:
        """Names like "Cart, 0 items" / "Inbox (12)" carry live counts the step never mentioned; a locator that
        bakes the number in breaks once the data changes. Add XPath variants on the stable prefix ("Cart"), which
        both engines and the generated scripts run; they are only kept if they still match exactly one element."""
        from .engines.selenium_engine import ROLE_XPATH, xq
        asked = set(re.findall(r"\d+", target or ""))
        out, seen = [], set()
        for l in locs:
            if l.strategy not in ("role", "text") or not l.value or l.shadow:
                continue
            nums = re.findall(r"\d+", l.value)
            if not nums or asked & set(nums):
                continue
            prefix = re.split(r"\d", l.value, maxsplit=1)[0].rstrip(" ,:;([{#-–·|/").strip()
            if len(prefix) < 2 or not re.search(r"[^\W\d_]", prefix):
                continue
            P = xq(prefix)
            if l.strategy == "role":
                cond = ROLE_XPATH.get(l.role or "", f'@role="{l.role}"')
                xp = f"//*[({cond}) and (starts-with(normalize-space(.), {P}) or starts-with(normalize-space(@aria-label), {P}))]"
            else:
                xp = f"//*[starts-with(normalize-space(.), {P}) and not(.//*[starts-with(normalize-space(.), {P})])]"
            if xp not in seen:
                seen.add(xp)
                out.append(l.model_copy(update={"strategy": "xpath", "value": xp, "role": None, "code": None}))
        return out

    def _verify(self, cand: dict, target: str = "") -> list[Locator]:
        """Keep only locators that the engine itself resolves to exactly this one element."""
        e = self.engine
        good: list[Locator] = []
        desc = e.describe(cand) or {}
        info = {**cand, **{k: v for k, v in desc.items() if k in ("locators", "frames", "shadow", "marker", "tag", "type")}}
        if desc.get("role") and desc.get("name") and not cand.get("name"):
            info["name"] = desc["name"]
        cand = info
        all_locs = L.to_locators(cand)
        all_locs = self._count_tolerant(all_locs, target) + all_locs
        if hasattr(e, "verify_locators"):
            # one in-page check of every locator, then a native find_elements confirmation of the winners
            try:
                flags = e.verify_locators(cand, all_locs) or []
            except Exception as ex:
                log.debug("batch verify failed: %s", ex)
                flags = []
            if flags:
                for loc, ok in zip(all_locs, flags):
                    if ok and len(good) < 3:
                        try:
                            if not good and e.count(loc) != 1:   # the step's primary: confirm with the engine itself
                                continue
                        except Exception:
                            continue
                        good.append(loc)
                if good:
                    return good
        from .engines.selenium_engine import by_for
        portable = lambda l: by_for(l, bool(l.shadow)) is not None
        for loc in all_locs:
            if len(good) >= 3 and (any(portable(g) for g in good) or not portable(loc)):
                continue           # enough, unless we still need one the other engine (Selenium) can run
            try:
                if e.count(loc) != 1:
                    continue
                if loc.strategy != "playwright":     # Playwright's generator already targets this exact element
                    m = e.marker(loc)
                    if m is not None and m != cand.get("marker"):
                        continue
                good.append(loc)
            except Exception as ex:
                log.debug("verify %s failed: %s", loc.label(), ex)
            # primary + 2 verified fallbacks, and at least one Selenium can run (portable scripts / replays)
            if len(good) >= 3 and any(portable(g) for g in good):
                break
        if not good:
            good = [l for l in all_locs if l.strategy in ("css", "xpath")][:1]
        if len(good) > 3:          # keep primary + 2, but make sure a portable one is among the kept
            port = next((g for g in good if portable(g)), None)
            good = good[:2] + [port] if port and port not in good[:3] else good[:3]
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
        elif loc is not None and loc.strategy == "point":
            x, y = (int(v) for v in loc.value.split(","))
            if a in (Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK):
                e.click_point(x, y, button="right" if a == Action.RIGHT_CLICK else "left", double=a == Action.DOUBLE_CLICK)
            elif a == Action.HOVER:
                e.hover_point(x, y)
            elif a in (Action.TYPE, Action.APPEND):
                e.click_point(x, y)
                e.type_text(v or "")
            else:
                raise StepError(f"'{a.value}' is not possible on an element located only visually (canvas)")
        elif a == Action.CLICK:
            e.click(loc)
        elif a == Action.DOUBLE_CLICK:
            e.click(loc, double=True)
        elif a == Action.RIGHT_CLICK:
            e.click(loc, button="right")
        elif a == Action.TYPE:
            if e.info(loc)["tag"] == "select":       # "set Country to India" on a <select>
                e.select(loc, [v or ""])
            else:
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
            info = e.info(loc)
            if info["tag"] == "select":
                e.select(loc, options)
            elif info["tag"] in ("input", "textarea") and info["type"] not in ("checkbox", "radio"):
                e.fill(loc, options[0] if options else "", clear=True)   # "choose 3 as quantity" on a number input
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
                try:
                    if a == Action.ASSERT_URL:
                        actual = e.url()
                    elif a == Action.ASSERT_TITLE:
                        actual = e.title()
                    elif a == Action.ASSERT_DIALOG:
                        actual = e.last_dialog or getattr(self, "_prev_dialog", "") or ""
                    else:
                        actual = e.text_of(loc) if loc else e.page_text()
                except Exception as ex:      # page navigating mid-check: try again until the timeout
                    log.debug("assertion read failed (%s); retrying", ex)
                    actual = actual or ""
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
        cfg = self.config.model_dump()
        (d / "test_playwright.py").write_text(scriptgen.playwright_script(resolved, cfg))
        (d / "test_selenium.py").write_text(scriptgen.selenium_script(resolved, cfg))
        # the standalone runtime the scripts use (report, video, screenshots, highlight) - no dependency on this tool
        (d / "autopilot_runtime.py").write_text(Path(__file__).with_name("script_runtime.py").read_text())
        (d / "data.json").write_text(json.dumps(safe_data, indent=2))
        summary.artifacts = {
            "report": "report.html", "script": "script.json", "playwright": "test_playwright.py",
            "selenium": "test_selenium.py", "data": "data.json", "summary": "summary.json",
        }
        if summary.video:
            summary.artifacts["video"] = summary.video
        # mask values, not the JSON text: a secret "2" must not turn "index": 2 into invalid JSON
        (d / "summary.json").write_text(json.dumps(self.mask.deep(summary.model_dump(mode="json")), indent=2))
        (d / "report.html").write_text(report.render(summary, resolved, d, self.mask), encoding="utf-8")

    def _emit(self, kind: str, **kw) -> None:
        try:
            self.on_event({"type": kind, "run_id": self.run_id, **kw})
        except Exception:
            pass


_VERBS = {
    Action.CLICK: ("click", "clicked"), Action.DOUBLE_CLICK: ("double-click", "double-clicked"),
    Action.RIGHT_CLICK: ("right-click", "right-clicked"), Action.TYPE: ("type", "typed"), Action.APPEND: ("type", "typed"),
    Action.PRESS: ("press key", "key pressed"), Action.HOVER: ("hover", "hovered"), Action.SELECT: ("select", "selected"),
    Action.CHECK: ("check", "checked"), Action.UNCHECK: ("uncheck", "unchecked"), Action.DRAG: ("drag", "dragged"),
    Action.UPLOAD: ("upload", "uploaded"), Action.SCROLL: ("scroll to", "scrolled to"), Action.WAIT: ("wait for", "appeared"),
    Action.ASSERT_VISIBLE: ("check visible", "visible"), Action.ASSERT_TEXT: ("check text", "text matches"),
}


def _number_in(target: str | None) -> int | None:
    m = re.search(r"\d+", target or "")
    return int(m.group(0)) if m else None


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
