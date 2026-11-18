"""Selenium engine: native By.ID / By.NAME / By.LINK_TEXT / By.CSS_SELECTOR / By.XPATH locators,
switch_to.frame() for iframes, WebElement.shadow_root for (nested) shadow roots.

Video: WebDriver has no recorder, so a background thread captures ~4 frames/second (commands are
serialised with a lock so it never races the test) and ffmpeg stitches them into video.webm.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from ..locators import SNAPSHOT_JS
from ..models import Locator
from .base import (DND_JS, INFO_JS, JS_VALUE_TYPES, PAGE_TEXT_JS, SET_VALUE_JS, Engine, chrome_binary,
                   localstorage_init_script)

KEYS = {
    "Enter": "ENTER", "Tab": "TAB", "Escape": "ESCAPE", "Space": "SPACE", "Backspace": "BACKSPACE",
    "Delete": "DELETE", "ArrowUp": "ARROW_UP", "ArrowDown": "ARROW_DOWN", "ArrowLeft": "ARROW_LEFT",
    "ArrowRight": "ARROW_RIGHT", "PageUp": "PAGE_UP", "PageDown": "PAGE_DOWN", "Home": "HOME", "End": "END",
    "Control": "CONTROL", "Shift": "SHIFT", "Alt": "ALT", "Meta": "META",
}


def _js(fn: str) -> str:
    """Turn an arrow function '(el, v) => {...}' into an execute_script body."""
    return f"return ({fn})(arguments[0], arguments[1]);"


def xq(s: str) -> str:
    """XPath string literal."""
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in s.split('"')) + ")"


def cq(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


ROLE_XPATH = {
    "button": 'self::button or @role="button" or (self::input and (@type="submit" or @type="button" or @type="reset" or @type="image"))',
    "link": '(self::a and @href) or @role="link"',
    "heading": "self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6 or @role=\"heading\"",
    "checkbox": '(self::input and @type="checkbox") or @role="checkbox"',
    "radio": '(self::input and @type="radio") or @role="radio"',
    "textbox": 'self::textarea or (self::input and (not(@type) or @type="text" or @type="email" or @type="tel" or @type="url")) or @role="textbox"',
    "searchbox": '(self::input and @type="search") or @role="searchbox"',
    "combobox": 'self::select or @role="combobox"',
    "listbox": 'self::select or @role="listbox"',
    "option": 'self::option or @role="option"',
    "img": 'self::img or @role="img"',
    "slider": '(self::input and @type="range") or @role="slider"',
    "spinbutton": '(self::input and @type="number") or @role="spinbutton"',
    "listitem": 'self::li or @role="listitem"',
}


def by_for(loc: Locator, in_shadow: bool):
    """Native Selenium (By, value) for a Locator. Inside shadow roots only CSS is allowed by WebDriver."""
    from selenium.webdriver.common.by import By
    s, v = loc.strategy, loc.value
    if s == "id":
        return (By.CSS_SELECTOR, f'[id="{cq(v)}"]') if in_shadow else (By.ID, v)
    if s == "name":
        return (By.CSS_SELECTOR, f'[name="{cq(v)}"]') if in_shadow else (By.NAME, v)
    if s == "testid":
        return (By.CSS_SELECTOR, f'[data-testid="{cq(v)}"]')
    if s in ("placeholder", "alt", "title"):
        return (By.CSS_SELECTOR, f'[{s}="{cq(v)}"]')
    if s == "css":
        return (By.CSS_SELECTOR, v)
    if in_shadow:
        return None                      # role / label / text / xpath can't be evaluated inside shadow roots
    if s == "xpath":
        return (By.XPATH, v)
    if s == "text":
        return (By.XPATH, f"//*[normalize-space(.)={xq(v)} and not(.//*[normalize-space(.)={xq(v)}])]")
    if s == "label":
        return (By.XPATH, f"//*[@id=//label[normalize-space(.)={xq(v)}]/@for] | "
                          f"//label[normalize-space(.)={xq(v)}]//*[self::input or self::select or self::textarea]")
    if s == "role":
        if loc.role == "link":
            return (By.LINK_TEXT, v)
        cond = ROLE_XPATH.get(loc.role or "", f'@role="{loc.role}"')
        name = (f"normalize-space(.)={xq(v)} or @aria-label={xq(v)} or @value={xq(v)} or @title={xq(v)} or @alt={xq(v)}"
                f" or @id=//label[normalize-space(.)={xq(v)}]/@for or ancestor::label[normalize-space(.)={xq(v)}]")
        return (By.XPATH, f"//*[({cond}) and ({name})]")
    return (By.CSS_SELECTOR, v)


class SeleniumEngine(Engine):
    name = "selenium"

    def start(self) -> None:
        from selenium import webdriver
        c = self.config
        b = c.browser
        if b == "firefox":
            opts = webdriver.FirefoxOptions()
            if c.headless:
                opts.add_argument("-headless")
            if c.profile_dir:
                opts.add_argument("-profile")
                opts.add_argument(c.profile_dir)
            opts.set_capability("unhandledPromptBehavior", "ignore")
            self.driver = webdriver.Firefox(options=opts)
        else:
            opts = webdriver.EdgeOptions() if b in ("edge", "msedge") else webdriver.ChromeOptions()
            if c.headless:
                opts.add_argument("--headless=new")
            for a in ("--no-sandbox", "--disable-dev-shm-usage", f"--window-size={c.viewport_width},{c.viewport_height}"):
                opts.add_argument(a)
            if c.user_agent:
                opts.add_argument(f"--user-agent={c.user_agent}")
            if c.locale:
                opts.add_argument(f"--lang={c.locale}")
            if c.profile_dir:   # real Chrome profile: saved passwords, SSO cookies, extensions
                opts.add_argument(f"--user-data-dir={c.profile_dir}")
                if c.profile_name:
                    opts.add_argument(f"--profile-directory={c.profile_name}")
            opts.set_capability("unhandledPromptBehavior", "ignore")
            if chrome_binary() and b not in ("edge", "msedge"):
                opts.binary_location = chrome_binary()
            service = None
            if os.getenv("AIAUTO_CHROMEDRIVER") and b not in ("edge", "msedge"):
                from selenium.webdriver.chrome.service import Service
                service = Service(os.getenv("AIAUTO_CHROMEDRIVER"))
            cls = webdriver.Edge if b in ("edge", "msedge") else webdriver.Chrome
            self.driver = cls(options=opts, service=service) if service else cls(options=opts)
        self.driver.set_window_size(c.viewport_width, c.viewport_height)
        self.driver.set_page_load_timeout(max(30, c.timeout_ms // 1000 * 3))
        self._chromium = b != "firefox" and hasattr(self.driver, "execute_cdp_cmd")
        self._apply_session(c.session_state)
        # serialise all WebDriver commands so the video thread never interleaves mid-command
        self._lock = threading.RLock()
        orig = self.driver.execute

        def locked(*a, **k):
            with self._lock:
                return orig(*a, **k)
        self.driver.execute = locked
        self.init_frames()
        self._stop_video = threading.Event()
        self._continuous = self._frame_mode
        if self._continuous:
            self._video_thread = threading.Thread(target=self._video_loop, daemon=True)
            self._video_thread.start()

    def _cdp(self, cmd: str, params: dict):
        return self.driver.execute_cdp_cmd(cmd, params) if hasattr(self.driver, "execute_cdp_cmd") else None

    def _apply_session(self, state: dict | None) -> None:
        if not state:
            return
        cookies = []
        for ck in state.get("cookies", []):
            c = {k: ck[k] for k in ("name", "value", "domain", "path", "secure", "httpOnly") if k in ck}
            if ck.get("expires", -1) and ck.get("expires", -1) > 0:
                c["expires"] = ck["expires"]
            if ck.get("sameSite") in ("Strict", "Lax", "None"):
                c["sameSite"] = ck["sameSite"]
            cookies.append(c)
        try:
            if cookies:
                self._cdp("Network.setCookies", {"cookies": cookies})
            js = localstorage_init_script(state)
            if js:
                self._cdp("Page.addScriptToEvaluateOnNewDocument", {"source": js})
        except Exception as e:  # non-Chromium browsers: cookies must be added per-domain after navigation
            self._pending_cookies = state.get("cookies", [])
            import logging
            logging.getLogger(__name__).warning("CDP session restore unavailable (%s); cookies added after navigation", e)

    def storage_state(self) -> dict | None:
        try:
            res = self._cdp("Network.getAllCookies", {}) or {}
            cookies = [{"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"],
                        "expires": -1 if c.get("session") else c.get("expires", -1), "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", False), "sameSite": c.get("sameSite", "Lax")} for c in res.get("cookies", [])]
        except Exception:
            cookies = [{"name": c["name"], "value": c["value"], "domain": c.get("domain", ""), "path": c.get("path", "/"),
                        "expires": c.get("expiry", -1), "httpOnly": c.get("httpOnly", False), "secure": c.get("secure", False),
                        "sameSite": c.get("sameSite", "Lax")} for c in self.driver.get_cookies()]
        origins = []
        try:
            self.driver.switch_to.default_content()
            ls = self.driver.execute_script(
                "const o=[]; for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i); o.push({name:k,value:localStorage.getItem(k)});}"
                " return [location.origin, o];")
            if ls and ls[1]:
                origins.append({"origin": ls[0], "localStorage": ls[1]})
        except Exception:
            pass
        return {"cookies": cookies, "origins": origins}

    # video --------------------------------------------------------------------
    def _video_loop(self):
        interval = 0.25
        while not self._stop_video.is_set():
            t0 = time.time()
            try:
                with self._lock:
                    png = self.driver.get_screenshot_as_png()
                p = self._frame_dir / f"{len(self._frames):05d}.png"
                p.write_bytes(png)
                self._frames.append((time.time(), p))
                self._last_png = png
                self.live_tick()
            except Exception:
                pass     # e.g. an alert is open
            self._stop_video.wait(max(0.0, interval - (time.time() - t0)))

    def live_image(self):
        png = getattr(self, "_last_png", None) if getattr(self, "_continuous", False) else None
        if png is None:
            with self._lock:
                png = self.driver.get_screenshot_as_png()
        self._last_png = None
        return "image/png", png

    def stop(self) -> str | None:
        if getattr(self, "_continuous", False):
            self._stop_video.set()
            self._video_thread.join(timeout=5)
        try:
            self.driver.quit()
        except Exception:
            pass
        return self.finish_frames()

    # page ----------------------------------------------------------------
    def _top(self):
        self.driver.switch_to.default_content()

    def goto(self, url):
        self._top()
        self.driver.get(url)
        pending = getattr(self, "_pending_cookies", None)
        if pending:
            for ck in pending:
                try:
                    self.driver.add_cookie({k: ck[k] for k in ("name", "value", "path", "secure") if k in ck})
                except Exception:
                    pass
            self._pending_cookies = None
            self.driver.refresh()
        self.frame()

    def back(self):
        self._top(); self.driver.back(); self.frame()

    def refresh(self):
        self._top(); self.driver.refresh(); self.frame()

    def url(self):
        return self.driver.current_url

    def title(self):
        self._top()
        return self.driver.title

    def page_text(self):
        self._top()
        return self.driver.execute_script(f"return ({PAGE_TEXT_JS})()")

    def snapshot(self):
        self._follow_new_window()
        self._top()
        cands = self.driver.execute_script(SNAPSHOT_JS)
        if self.config.use_ax_tree and getattr(self, "_chromium", False):
            try:   # real roles + accessible names from Chrome's accessibility tree (CDP)
                from .ax import enrich
                self.ax_hits = enrich(self._cdp, self.driver.execute_script, cands)
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("AX tree enrichment skipped: %s", e)
        return cands

    def screenshot(self, path, full_page=False):
        self.driver.save_screenshot(str(path))

    def after_action(self):
        """Selenium leaves alerts open (unhandledPromptBehavior=ignore): record + answer them here."""
        try:
            alert = self.driver.switch_to.alert
            text = alert.text
        except Exception:
            return
        self.last_dialog = f"dialog: {text}"
        try:
            alert.accept() if self.dialog_policy == "accept" else alert.dismiss()
        except Exception:
            pass

    def wait_for_load(self):
        self.after_action()
        end = time.time() + self.config.timeout_ms / 1000
        while time.time() < end:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    break
            except Exception:
                self.after_action()
            time.sleep(0.1)
        self._follow_new_window()

    def _follow_new_window(self):
        try:
            handles = self.driver.window_handles
            if handles and len(handles) > getattr(self, "_known_handles", 1):
                self.driver.switch_to.window(handles[-1])
            self._known_handles = len(handles)
        except Exception:
            pass

    def switch_tab(self, which):
        handles = self.driver.window_handles
        self.driver.switch_to.window(handles[-1] if which in ("last", "new", "", None) else handles[int(which)])
        self.frame()

    def press(self, key, locator=None):
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        keys = [getattr(Keys, KEYS[p]) if p in KEYS else p.lower() for p in key.split("+")]
        if locator:
            self._el(locator).send_keys("".join(keys))  # modifiers stay held for the rest of the string
        else:
            self._top()
            ac = ActionChains(self.driver)
            for k in keys[:-1]:
                ac.key_down(k)
            ac.send_keys(keys[-1])
            for k in reversed(keys[:-1]):
                ac.key_up(k)
            ac.perform()
        self.frame()

    def scroll(self, direction, amount=600):
        self._top()
        js = {"top": "window.scrollTo(0,0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)",
              "up": f"window.scrollBy(0,-{amount})"}.get(direction, f"window.scrollBy(0,{amount})")
        self.driver.execute_script(js)
        self.frame()

    # locators -------------------------------------------------------------
    def _context(self, loc: Locator):
        """Enter the iframe chain, then walk the shadow-host chain; returns a searchable context."""
        from selenium.webdriver.common.by import By
        d = self.driver
        d.switch_to.default_content()
        for f in loc.frames:
            d.switch_to.frame(d.find_element(By.CSS_SELECTOR, f))
        ctx = d
        for host in loc.shadow:
            ctx = ctx.find_element(By.CSS_SELECTOR, host).shadow_root
        return ctx

    def _find_all(self, loc: Locator):
        ctx = self._context(loc)
        by = by_for(loc, bool(loc.shadow))
        if by is None:
            return []
        return ctx.find_elements(*by)

    def count(self, loc):
        return len(self._find_all(loc))

    def _el(self, loc: Locator, timeout_ms: int | None = None):
        end = time.time() + (timeout_ms if timeout_ms is not None else self.config.timeout_ms) / 1000
        last = None
        while True:
            try:
                els = self._find_all(loc)
                if els:
                    return els[0]
            except Exception as e:
                last = e
            if time.time() >= end:
                raise LookupError(f"element not found: {loc.label()}" + (f" ({last})" if last else ""))
            time.sleep(0.25)

    def marker(self, loc):
        return self.driver.execute_script("return arguments[0].__aiautoId || null;", self._el(loc, 2000))

    def info(self, loc):
        return self.driver.execute_script(_js(INFO_JS), self._el(loc))

    def run_js_on(self, loc, fn, arg=None):
        return self.driver.execute_script(_js(fn), self._el(loc), arg)

    def _wait_clickable(self, el):
        end = time.time() + self.config.timeout_ms / 1000
        while time.time() < end:
            try:
                if el.is_displayed() and el.is_enabled():
                    return
            except Exception:
                return
            time.sleep(0.2)

    # actions --------------------------------------------------------------
    def click(self, loc, button="left", double=False):
        from selenium.webdriver.common.action_chains import ActionChains
        el = self._el(loc)
        self._wait_clickable(el)
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'})", el)
        if double:
            ActionChains(self.driver).double_click(el).perform()
        elif button == "right":
            ActionChains(self.driver).context_click(el).perform()
        else:
            try:
                el.click()
            except Exception:
                self.driver.execute_script("arguments[0].click()", el)
        self.frame()

    def fill(self, loc, text, clear=True):
        el = self._el(loc)
        info = self.driver.execute_script(_js(INFO_JS), el)
        if info["tag"] == "input" and info["type"] in JS_VALUE_TYPES:
            self.driver.execute_script(_js(SET_VALUE_JS), el, text)
        elif info.get("editable") and info["tag"] not in ("input", "textarea"):
            if clear:
                self.driver.execute_script("arguments[0].textContent = ''", el)
            el.click()
            el.send_keys(text)
        else:
            if clear:
                el.clear()
            el.send_keys(text)
        self.frame()

    def hover(self, loc):
        from selenium.webdriver.common.action_chains import ActionChains
        el = self._el(loc)
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
        ActionChains(self.driver).move_to_element(el).perform()
        self.frame()

    def select(self, loc, options):
        from selenium.webdriver.support.ui import Select
        s = Select(self._el(loc))
        if s.is_multiple:
            s.deselect_all()
        for o in options:
            try:
                s.select_by_visible_text(o)
            except Exception:
                s.select_by_value(o)
        self.frame()

    def set_checked(self, loc, checked):
        el = self._el(loc)
        if el.is_selected() != checked:
            try:
                el.click()
            except Exception:
                self.driver.execute_script("arguments[0].click()", el)
        self.frame()

    def text_of(self, loc):
        el = self._el(loc)
        if el.tag_name.lower() in ("input", "textarea", "select"):
            return el.get_attribute("value") or ""
        return el.text or self.driver.execute_script("return arguments[0].textContent", el)

    def scroll_into_view(self, loc):
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", self._el(loc))
        self.frame()

    def upload(self, loc, path):
        self._el(loc).send_keys(os.path.abspath(path)); self.frame()

    def drag(self, src, dst):
        from selenium.webdriver.common.action_chains import ActionChains
        if src.frames != dst.frames or src.shadow != dst.shadow:
            raise RuntimeError("drag between different frames/shadow roots is not supported")
        s = self._el(src)
        d = self._el(dst)
        if self.driver.execute_script("return arguments[0].getAttribute('draggable') === 'true'", s):
            self.driver.execute_script(_js(DND_JS), s, d)    # WebDriver actions can't trigger HTML5 DnD
        else:
            ActionChains(self.driver).drag_and_drop(s, d).perform()
        self.frame()
