from __future__ import annotations

import shutil
from pathlib import Path

from ..locators import SNAPSHOT_JS
from ..models import Locator
from .base import HIGHLIGHT_JS, Engine, chrome_binary


def _sel(loc: Locator) -> str:
    return f"xpath={loc.value}" if loc.strategy == "xpath" else f"css={loc.value}"


class PlaywrightEngine(Engine):
    name = "playwright"

    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        browser_type = {"firefox": self._pw.firefox, "webkit": self._pw.webkit}.get(self.config.browser, self._pw.chromium)
        launch = {"headless": self.config.headless}
        if browser_type is self._pw.chromium and chrome_binary():
            launch["executable_path"] = chrome_binary()
        if self.config.browser in ("chrome", "msedge"):
            launch["channel"] = self.config.browser
        self._browser = browser_type.launch(**launch)
        vp = {"width": self.config.viewport_width, "height": self.config.viewport_height}
        ctx = {"viewport": vp}
        if self.config.record_video:
            self._video_dir = self.run_dir / "_video"
            ctx.update(record_video_dir=str(self._video_dir), record_video_size=vp)
        self._native_video = self.config.record_video
        self._frame_mode = False
        try:
            self._ctx = self._browser.new_context(**ctx)
            self.page = self._ctx.new_page()
        except Exception as e:
            if "ffmpeg" not in str(e).lower():
                raise
            # Playwright's recorder needs its own ffmpeg build; fall back to frame capture.
            ctx.pop("record_video_dir", None); ctx.pop("record_video_size", None)
            self._native_video = False
            self._ctx = self._browser.new_context(**ctx)
            self.page = self._ctx.new_page()
            self.init_frames()
        self._ctx.set_default_timeout(self.config.timeout_ms)
        # follow popups / new tabs automatically
        self._ctx.on("page", self._on_new_page)

    def _on_new_page(self, page) -> None:
        self.page = page

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
                dst = self.run_dir / "video.webm"
                shutil.move(str(src), dst)
                video = dst.name
        finally:
            self._browser.close()
            self._pw.stop()
            shutil.rmtree(self.run_dir / "_video", ignore_errors=True)
        return video

    def goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded"); self.frame()
    def back(self):
        self.page.go_back(); self.frame()
    def refresh(self):
        self.page.reload(); self.frame()
    def url(self): return self.page.url
    def title(self): return self.page.title()
    def page_text(self): return self.page.inner_text("body")
    def snapshot(self): return self.page.evaluate("() => {" + SNAPSHOT_JS + "}")
    def screenshot(self, path, full_page=False): self.page.screenshot(path=str(path), full_page=full_page)

    def wait_for_load(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=self.config.timeout_ms)
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass

    def press(self, key, locator=None):
        if locator:
            self.page.press(_sel(locator), key)
        else:
            self.page.keyboard.press(key)
        self.frame()

    def scroll(self, direction, amount=600):
        js = {"top": "window.scrollTo(0,0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)",
              "up": f"window.scrollBy(0,-{amount})"}.get(direction, f"window.scrollBy(0,{amount})")
        self.page.evaluate(js)
        self.frame()

    def exists(self, locator, timeout_ms):
        try:
            self.page.locator(_sel(locator)).first.wait_for(state="attached", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def _loc(self, locator):
        return self.page.locator(_sel(locator)).first

    def click(self, locator, button="left", double=False):
        loc = self._loc(locator)
        loc.dblclick() if double else loc.click(button=button)
        self.frame()

    def fill(self, locator, text, clear=True):
        loc = self._loc(locator)
        if clear:
            loc.fill(text)
        else:
            loc.press_sequentially(text)
        self.frame()

    def hover(self, locator):
        self._loc(locator).hover(); self.frame()

    def select(self, locator, option):
        loc = self._loc(locator)
        try:
            loc.select_option(label=option)
        except Exception:
            loc.select_option(value=option)
        self.frame()

    def set_checked(self, locator, checked):
        self._loc(locator).set_checked(checked, force=True)
        self.frame()

    def text_of(self, locator):
        loc = self._loc(locator)
        if loc.evaluate("e => ['INPUT','TEXTAREA','SELECT'].includes(e.tagName)"):
            return loc.input_value()
        return loc.inner_text()

    def scroll_into_view(self, locator): self._loc(locator).scroll_into_view_if_needed()
    def upload(self, locator, path):
        self._loc(locator).set_input_files(path); self.frame()

    def highlight(self, locator):
        try:
            self._loc(locator).evaluate(HIGHLIGHT_JS, timeout=1000)
            self.frame()
        except Exception:
            pass
