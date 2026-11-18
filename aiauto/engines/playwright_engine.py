"""Playwright engine: native get_by_role / get_by_label / get_by_text / get_by_test_id ... locators,
frame_locator() for iframes, chained locators for (open) shadow roots, native video recording."""
from __future__ import annotations

import shutil
from pathlib import Path

import logging
import uuid

from ..locators import CSS_PATH_JS, DESCRIBE_EL_JS, snapshot_js
from ..models import Locator
from .base import (OPEN_SHADOW_JS, INFO_JS, JS_VALUE_TYPES, PAGE_TEXT_JS, SET_VALUE_JS, Engine, chrome_binary,
                   localstorage_init_script)


def css_str(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


class PlaywrightEngine(Engine):
    name = "playwright"

    @property
    def has_dom_fallback(self) -> bool:      # ARIA snapshot first, DOM walk for elements it doesn't expose
        return self.config.use_playwright_snapshot

    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        c = self.config
        self._pw = sync_playwright().start()
        btype = {"firefox": self._pw.firefox, "webkit": self._pw.webkit}.get(c.browser, self._pw.chromium)
        launch: dict = {"headless": c.headless}
        if c.slow_mo_ms:
            launch["slow_mo"] = c.slow_mo_ms
        if btype is self._pw.chromium:
            if c.browser in ("chrome", "msedge", "chrome-beta"):
                launch["channel"] = c.browser
            elif chrome_binary():
                launch["executable_path"] = chrome_binary()
        vp = {"width": c.viewport_width, "height": c.viewport_height}
        ctx: dict = {"viewport": vp, "accept_downloads": True}
        if c.user_agent:
            ctx["user_agent"] = c.user_agent
        if c.locale:
            ctx["locale"] = c.locale
        self._native_video = c.record_video
        self._frame_mode = False
        if c.record_video:
            ctx.update(record_video_dir=str(self.run_dir / "_video"), record_video_size=vp)

        def open_ctx(opts):
            if c.profile_dir:   # real Chrome profile: saved passwords, SSO cookies, extensions
                args = list(launch.get("args", []))
                if c.profile_name:
                    args.append(f"--profile-directory={c.profile_name}")
                self._browser = None
                return btype.launch_persistent_context(c.profile_dir, **{**launch, "args": args}, **opts)
            self._browser = btype.launch(**launch)
            if c.session_state:
                opts = {**opts, "storage_state": c.session_state}
            return self._browser.new_context(**opts)

        try:
            self._ctx = open_ctx(ctx)
            self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        except Exception as e:
            if "ffmpeg" not in str(e).lower():
                raise
            # Playwright's recorder needs its own ffmpeg build; fall back to frame capture.
            try:
                (self._browser or self._ctx).close()
            except Exception:
                pass
            ctx.pop("record_video_dir", None)
            ctx.pop("record_video_size", None)
            self._native_video = False
            self._ctx = open_ctx(ctx)
            self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self.init_frames()
        if c.profile_dir and c.session_state:
            if c.session_state.get("cookies"):
                self._ctx.add_cookies(c.session_state["cookies"])
            js = localstorage_init_script(c.session_state)
            if js:
                self._ctx.add_init_script(js)
        if c.open_closed_shadow_roots:
            self._ctx.add_init_script(OPEN_SHADOW_JS)
        self._ctx.set_default_timeout(c.timeout_ms)
        self._ctx.on("page", self._on_new_page)
        self._watch(self.page)

    def _watch(self, page) -> None:
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog) -> None:
        self.last_dialog = f"{dialog.type}: {dialog.message}"
        try:
            dialog.accept() if self.dialog_policy == "accept" else dialog.dismiss()
        except Exception:
            pass

    def _on_new_page(self, page) -> None:
        self._watch(page)
        self.page = page   # follow popups / new tabs automatically

    def storage_state(self) -> dict | None:
        try:
            return self._ctx.storage_state()
        except Exception:
            return None

    def stop(self) -> str | None:
        video = None
        try:
            self.frame()
            vid = self.page.video if self._native_video else None
            self._ctx.close()
            if self._frame_mode:
                video = self.finish_frames()
            if vid:
                src = Path(vid.path())
                if src.exists():
                    dst = self.run_dir / "video.webm"
                    shutil.move(str(src), dst)
                    video = dst.name
        finally:
            if self._browser:
                self._browser.close()
            self._pw.stop()
            shutil.rmtree(self.run_dir / "_video", ignore_errors=True)
        return video

    # page -----------------------------------------------------------------
    def goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded")
        self.frame()

    def back(self):
        self.page.go_back(); self.frame()

    def refresh(self):
        self.page.reload(); self.frame()

    def url(self): return self.page.url
    def title(self): return self.page.title()
    def page_text(self): return self.page.evaluate(PAGE_TEXT_JS)
    def snapshot(self, words=None):
        """Playwright's own accessibility snapshot (roles/names per the ARIA spec, shadow DOM + iframes)."""
        if self.config.use_playwright_snapshot:
            try:
                from .aria import parse
                return parse(self.page.aria_snapshot(mode="ai"))
            except Exception as e:
                logging.getLogger(__name__).debug("aria snapshot failed, using DOM snapshot: %s", e)
        return self.snapshot_dom(words)

    def snapshot_dom(self, words=None):
        return self.page.evaluate("() => {" + snapshot_js(words) + "}")

    def click_point(self, x, y, button="left", double=False):
        self.page.mouse.click(x, y, button=button, click_count=2 if double else 1)
        self.frame()

    def hover_point(self, x, y):
        self.page.mouse.move(x, y); self.frame()

    def type_text(self, text):
        self.page.keyboard.type(text); self.frame()

    def viewport(self):
        vs = self.page.viewport_size or {}
        return vs.get("width", self.config.viewport_width), vs.get("height", self.config.viewport_height)

    def eval_js(self, script):
        return self.page.evaluate("() => {" + script + "}")

    def describe(self, cand):
        marker = cand.get("marker") or f"pw{uuid.uuid4().hex[:8]}:{cand['uid']}"
        if cand.get("ref"):
            loc = self.page.get_by_ref(cand["ref"])
            d = loc.evaluate(DESCRIBE_EL_JS, marker, timeout=5000)
            if not d.get("frames"):
                # cross-origin iframes hide their parents from page scripts: build the iframe chain via Playwright
                try:
                    chain, fr = [], loc.element_handle(timeout=3000).owner_frame()
                    while fr and fr.parent_frame:
                        chain.insert(0, fr.frame_element().evaluate(CSS_PATH_JS))
                        fr = fr.parent_frame
                    d["frames"] = chain
                except Exception as e:
                    logging.getLogger(__name__).debug("frame chain failed: %s", e)
            try:   # Playwright's own locator generator (as in codegen): unique by construction
                n = loc.normalize()
                impl = getattr(n, "_impl_obj", n)
                d["locators"].insert(0, {"strategy": "playwright", "value": impl._selector,
                                         "code": getattr(impl, "_locator_code", None)})
            except Exception as e:
                logging.getLogger(__name__).debug("normalize failed: %s", e)
            # Playwright's ARIA role/name are authoritative for the role locator
            if cand.get("role") and cand.get("name"):
                d["role"], d["name"] = cand["role"], cand["name"]
            return d
        return self.page.evaluate(
            "([uid, marker]) => { const el = (window.__aiautoEls || [])[uid - 1]; return el ? (" + DESCRIBE_EL_JS + ")(el, marker) : null; }",
            [cand["uid"], marker])

    def screenshot(self, path, full_page=False):
        self.page.screenshot(path=str(path), full_page=full_page, timeout=15000)

    def live_image(self):
        return "image/jpeg", self.page.screenshot(type="jpeg", quality=50, scale="css", timeout=3000)

    def wait_for_load(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=self.config.timeout_ms)
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass

    def switch_tab(self, which):
        pages = self._ctx.pages
        self.page = pages[-1] if which in ("last", "new", "", None) else pages[int(which)]
        self.page.bring_to_front()
        self.frame()

    def press(self, key, locator=None):
        if locator:
            self.native(locator).first.press(key)
        else:
            self.page.keyboard.press(key)
        self.frame()

    def scroll(self, direction, amount=600):
        js = {"top": "window.scrollTo(0,0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)",
              "up": f"window.scrollBy(0,-{amount})"}.get(direction, f"window.scrollBy(0,{amount})")
        self.page.evaluate(js)
        self.frame()

    # locators -------------------------------------------------------------
    def native(self, loc: Locator):
        """Map an engine-agnostic Locator onto Playwright's native locator API."""
        base = self.page
        for f in loc.frames:
            base = base.frame_locator(f)
        for host in loc.shadow:          # Playwright CSS pierces open shadow roots; chain through hosts
            base = base.locator(host)
        s, v = loc.strategy, loc.value
        if s == "playwright":          # selector from Playwright's generator; frames/shadow are encoded in it
            return self.page.locator(v)
        if s == "testid":
            return base.get_by_test_id(v)
        if s == "id":
            return base.locator(f'[id="{css_str(v)}"]')
        if s == "role":
            return base.get_by_role(loc.role, name=v, exact=True)
        if s == "label":
            return base.get_by_label(v, exact=True)
        if s == "placeholder":
            return base.get_by_placeholder(v, exact=True)
        if s == "text":
            return base.get_by_text(v, exact=True)
        if s == "alt":
            return base.get_by_alt_text(v, exact=True)
        if s == "title":
            return base.get_by_title(v, exact=True)
        if s == "name":
            return base.locator(f'[name="{css_str(v)}"]')
        if s == "xpath":
            return base.locator(f"xpath={v}")
        return base.locator(v)

    def _one(self, loc):
        return self.native(loc).first

    def count(self, loc):
        return self.native(loc).count()

    def marker(self, loc):
        return self._one(loc).evaluate("e => e.__aiautoId || null", timeout=2000)

    def info(self, loc):
        return self._one(loc).evaluate(INFO_JS)

    def run_js_on(self, loc, fn, arg=None, timeout_ms=None):
        return self._one(loc).evaluate(fn, arg, timeout=timeout_ms or self.config.timeout_ms)

    # actions --------------------------------------------------------------
    def click(self, loc, button="left", double=False):
        el = self._one(loc)
        el.dblclick() if double else el.click(button=button)
        self.frame()

    def fill(self, loc, text, clear=True):
        el = self._one(loc)
        info = el.evaluate(INFO_JS)
        if info["tag"] == "input" and info["type"] in JS_VALUE_TYPES:
            el.evaluate(SET_VALUE_JS, text)
        elif clear:
            el.fill(text)
        else:
            el.press_sequentially(text)
        self.frame()

    def hover(self, loc):
        self._one(loc).hover(); self.frame()

    def select(self, loc, options):
        el = self._one(loc)
        try:
            el.select_option(label=options)
        except Exception:
            el.select_option(value=options)
        self.frame()

    def set_checked(self, loc, checked):
        el = self._one(loc)
        try:
            el.set_checked(checked)
        except Exception:
            el.set_checked(checked, force=True)
        self.frame()

    def text_of(self, loc):
        el = self._one(loc)
        if el.evaluate("e => ['INPUT','TEXTAREA','SELECT'].includes(e.tagName)"):
            return el.input_value()
        return el.inner_text()

    def scroll_into_view(self, loc):
        self._one(loc).scroll_into_view_if_needed(); self.frame()

    def upload(self, loc, path):
        self._one(loc).set_input_files(path); self.frame()

    def drag(self, src, dst):
        self._one(src).drag_to(self._one(dst))   # native mouse-driven drag (works for HTML5 DnD too)
        self.frame()
