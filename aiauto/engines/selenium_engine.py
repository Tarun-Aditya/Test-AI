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

from ..locators import DESCRIBE_EL_JS, VERIFY_JS, snapshot_js
from ..models import Locator
from .base import (OPEN_SHADOW_JS, DND_JS, INFO_JS, JS_VALUE_TYPES, PAGE_TEXT_JS, SET_VALUE_JS, Engine, chrome_binary,
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
    if s in ("playwright", "point"):
        return None                      # Playwright-only selector / screen position: not a Selenium locator
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
        cond = ROLE_XPATH.get(loc.role or "", f'@role="{loc.role}"')
        name = (f"normalize-space(.)={xq(v)} or @aria-label={xq(v)} or @value={xq(v)} or @title={xq(v)} or @alt={xq(v)}"
                f" or @id=//label[normalize-space(.)={xq(v)}]/@for or ancestor::label[normalize-space(.)={xq(v)}]")
        return (By.XPATH, f"//*[({cond}) and ({name})]")
    return (By.CSS_SELECTOR, v)


class SeleniumEngine(Engine):
    name = "selenium"

    def start(self) -> None:
        from selenium import webdriver
        from ..script_runtime import selenium_browser, selenium_service
        c = self.config
        b = c.browser
        if c.remote_url and (b or "").lower() in ("safari", "webkit"):
            kind, binary, driver = "safari", None, None     # Safari on a cloud Mac: nothing to resolve locally
        else:
            kind, binary, driver = selenium_browser(b)
        service = None if c.remote_url else selenium_service(kind, driver, binary)
        if c.remote_url:             # Selenium Grid / cloud: the browser runs there, not here
            binary, service = None, None
        if kind == "safari":         # macOS: safaridriver (enable once: sudo safaridriver --enable); no headless mode
            opts = webdriver.SafariOptions()
            self.driver = self._remote(opts) if c.remote_url else webdriver.Safari(options=opts)
        elif kind == "firefox":
            opts = webdriver.FirefoxOptions()
            if binary:
                opts.binary_location = binary
            if c.headless:
                opts.add_argument("-headless")
            if c.profile_dir:
                opts.add_argument("-profile")
                opts.add_argument(c.profile_dir)
            opts.set_capability("unhandledPromptBehavior", "ignore")
            if c.remote_url:
                self.driver = self._remote(opts)
            else:
                self.driver = webdriver.Firefox(options=opts, service=service) if service else webdriver.Firefox(options=opts)
        else:
            opts = webdriver.EdgeOptions() if kind == "edge" else webdriver.ChromeOptions()
            if c.headless:
                opts.add_argument("--headless=new")
            # Chrome features that put browser UI over the page, which an automated browser can't dismiss
            # (Playwright's Chromium launches with them off, so the same test behaves the same on both engines):
            # - HTTPS-First turns plain http:// pages into an empty interstitial
            # - the password leak check ("Change your password", after signing in with a known password such as a
            #   demo site's) swallows every click on the next page
            for a in ("--no-sandbox", "--disable-dev-shm-usage", f"--window-size={c.viewport_width},{c.viewport_height}",
                      "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable,HttpsFirstModeV2ForEngagedSites,"
                      "PasswordLeakDetection,PasswordCheck"):
                opts.add_argument(a)
            prefs = {"https_only_mode_enabled": False, "https_upgrades_enabled": False, "profile.password_manager_leak_detection": False}
            if not c.profile_dir:            # a real profile keeps its saved passwords; a fresh one offers no save bubble
                prefs.update({"credentials_enable_service": False, "profile.password_manager_enabled": False})
            opts.add_experimental_option("prefs", prefs)
            if c.user_agent:
                opts.add_argument(f"--user-agent={c.user_agent}")
            if c.locale:
                opts.add_argument(f"--lang={c.locale}")
            if c.profile_dir:   # real Chrome profile: saved passwords, SSO cookies, extensions
                opts.add_argument(f"--user-data-dir={c.profile_dir}")
                if c.profile_name:
                    opts.add_argument(f"--profile-directory={c.profile_name}")
            opts.set_capability("unhandledPromptBehavior", "ignore")
            if binary:
                opts.binary_location = binary
            cls = webdriver.Edge if kind == "edge" else webdriver.Chrome
            if c.remote_url:
                self.driver = self._remote(opts)
            else:
                self.driver = cls(options=opts, service=service) if service else cls(options=opts)
        self.driver.set_window_size(c.viewport_width, c.viewport_height)
        self.driver.set_page_load_timeout(max(30, c.timeout_ms // 1000 * 3))
        self._chromium = kind != "firefox" and hasattr(self.driver, "execute_cdp_cmd")
        self._apply_session(c.session_state)
        if c.open_closed_shadow_roots and self._chromium:
            try:
                self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": OPEN_SHADOW_JS})
            except Exception:
                pass
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

    def _remote(self, opts):
        """webdriver.Remote against a grid; capabilities (platformName "Windows 11" / "macOS", browserVersion,
        vendor options such as bstack:options / sauce:options / LT:Options) come from remote_caps."""
        from selenium import webdriver
        for k, v in (self.config.remote_caps or {}).items():
            opts.set_capability(k, v)
        return webdriver.Remote(command_executor=self.config.remote_url, options=opts)

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
                    img, ext = self._grab_fast()
                p = self._frame_dir / f"{len(self._frames):05d}.{ext}"
                p.write_bytes(img)
                self._frames.append((time.time(), p))
                self._last_png = (ext, img)
                self.live_tick()
            except Exception:
                pass     # e.g. an alert is open
            self._stop_video.wait(max(0.0, interval - (time.time() - t0)))

    def _grab_fast(self):
        """JPEG via CDP on Chromium (much cheaper to encode than WebDriver's PNG); PNG otherwise."""
        if getattr(self, "_chromium", False):
            try:
                import base64
                data = self.driver.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": 70})["data"]
                return base64.b64decode(data), "jpg"
            except Exception:
                pass
        return self.driver.get_screenshot_as_png(), "png"

    def live_image(self):
        last = getattr(self, "_last_png", None) if getattr(self, "_continuous", False) else None
        if last is None:
            with self._lock:
                last = self._grab_fast()[::-1]
        self._last_png = None
        ext, img = last
        return ("image/jpeg" if ext == "jpg" else "image/png"), img

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

    def _enter(self, chain):
        from selenium.webdriver.common.by import By
        self.driver.switch_to.default_content()
        for css in chain:
            self.driver.switch_to.frame(self.driver.find_element(By.CSS_SELECTOR, css))

    def snapshot(self, words=None):
        """DOM walk of the page; cross-origin iframes (which page scripts can't look into) are entered with
        switch_to.frame and walked from inside, nested ones too."""
        self._follow_new_window()
        self._top()
        js = snapshot_js(words)
        out = self.driver.execute_script(js) or []
        queue = list(self.driver.execute_script("return window.__aiautoBlocked || []") or [])
        n = 0
        while queue and n < 25:
            chain = queue.pop(0)
            n += 1
            try:
                self._enter(chain)
                cands = self.driver.execute_script(js) or []
                nested = self.driver.execute_script("return window.__aiautoBlocked || []") or []
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("could not enter frame %s: %s", chain, e)
                continue
            off = 100000 * n
            for c in cands:
                c["xchain"], c["local_uid"], c["uid"] = chain, c["uid"], off + c["uid"]
                c["frames"] = chain + c.get("frames", [])
                c["anc"] = [off + a for a in c.get("anc", [])]
            out.extend(cands)
            queue.extend(chain + nb for nb in nested)
        self._top()
        return out

    def _local(self, cand):
        """Enter the candidate's cross-origin frame (if any); return its uid in that frame's window."""
        if cand.get("xchain"):
            self._enter(cand["xchain"])
            return cand["local_uid"]
        self._top()
        return cand["uid"]

    def verify_locators(self, cand, locs):
        """One in-page call: which of these locators (as Selenium would run them) match exactly this element."""
        specs = []
        for l in locs:
            by = by_for(l, bool(l.shadow))
            specs.append([by[0], by[1]] if by else None)
        try:
            return self.driver.execute_script(VERIFY_JS, self._local(cand), specs)
        finally:
            self._top()

    def refine(self, cands):
        """After a first ranking: Chrome's real role + accessible name (CDP) for the top candidates."""
        if not (self.config.use_ax_tree and getattr(self, "_chromium", False)):
            return 0
        from .ax import refine
        # cross-origin frames run in another process: their elements keep the snapshot's values
        self.ax_hits = refine(self._cdp, [c for c in cands if not c.get("xchain")])
        return self.ax_hits

    def _pointer(self, x, y):
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        ab = ActionBuilder(self.driver)
        ab.pointer_action.move_to_location(int(x), int(y))
        return ab

    def click_point(self, x, y, button="left", double=False):
        self._top()
        ab = self._pointer(x, y)
        if button == "right":
            ab.pointer_action.context_click()
        else:
            ab.pointer_action.click()
            if double:
                ab.pointer_action.click()
        ab.perform()
        self.frame()

    def hover_point(self, x, y):
        self._top(); self._pointer(x, y).perform(); self.frame()

    def type_text(self, text):
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(self.driver).send_keys(text).perform(); self.frame()

    def viewport(self):
        self._top()
        w, h = self.driver.execute_script("return [innerWidth, innerHeight]")
        return int(w), int(h)

    def eval_js(self, script):
        self._top()
        return self.driver.execute_script(script)

    def describe(self, cand):
        try:
            d = self.driver.execute_script(
                "const el = (window.__aiautoEls || [])[arguments[0] - 1]; if (!el) return null; return (" + DESCRIBE_EL_JS + ")(el, arguments[1]);",
                self._local(cand), cand.get("marker"))
        finally:
            self._top()
        if d and cand.get("xchain"):
            d["frames"] = cand["xchain"] + d.get("frames", [])
        if d and cand.get("ax"):        # keep Chrome's accessible role/name (CDP) for the role locator
            from .ax import _rebuild_role_locator
            d["role"], d["name"] = cand.get("role"), cand.get("name")
            _rebuild_role_locator(d)
        return d

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

    def run_js_on(self, loc, fn, arg=None, timeout_ms=None):
        return self.driver.execute_script(_js(fn), self._el(loc, timeout_ms), arg)

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
        # 'nearest' doesn't move an element that is already visible (moving it re-renders virtual lists)
        self.driver.execute_script("arguments[0].scrollIntoView({block:'nearest', inline:'nearest'})", el)
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
                try:
                    s.select_by_value(o)
                except Exception:
                    # a transparent, custom-skinned <select> Selenium won't click: choose it the way the page would
                    ok = self.driver.execute_script("""const s = arguments[0], want = arguments[1].trim();
                      const opt = [...s.options].find(x => x.text.trim() === want || x.value === want);
                      if (!opt) return false; s.value = opt.value;
                      s.dispatchEvent(new Event('input', {bubbles: true})); s.dispatchEvent(new Event('change', {bubbles: true})); return true;""",
                                                    s._el, o)
                    if not ok:
                        raise
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


def _stale_retry(fn):
    """Re-find and retry when the page re-rendered the element between finding and acting (stale reference)."""
    import functools

    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        from selenium.common.exceptions import StaleElementReferenceException
        for attempt in range(3):
            try:
                return fn(self, *a, **k)
            except StaleElementReferenceException:
                if attempt == 2:
                    raise
                time.sleep(0.15)
    return wrapper


for _name in ("click", "fill", "hover", "select", "set_checked", "text_of", "scroll_into_view", "upload", "drag",
              "run_js_on", "info", "marker", "press"):
    setattr(SeleniumEngine, _name, _stale_retry(getattr(SeleniumEngine, _name)))
