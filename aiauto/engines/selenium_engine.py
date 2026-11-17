from __future__ import annotations

import os
import time
from pathlib import Path

from ..locators import SNAPSHOT_JS
from ..models import Locator
from .base import Engine, chrome_binary

KEYS = {
    "Enter": "ENTER", "Tab": "TAB", "Escape": "ESCAPE", "Space": "SPACE", "Backspace": "BACKSPACE",
    "Delete": "DELETE", "ArrowUp": "ARROW_UP", "ArrowDown": "ARROW_DOWN", "ArrowLeft": "ARROW_LEFT",
    "ArrowRight": "ARROW_RIGHT", "PageUp": "PAGE_UP", "PageDown": "PAGE_DOWN", "Home": "HOME", "End": "END",
    "Control": "CONTROL", "Shift": "SHIFT", "Alt": "ALT", "Meta": "META",
}


def _by(loc: Locator):
    from selenium.webdriver.common.by import By
    return (By.XPATH, loc.value) if loc.strategy == "xpath" else (By.CSS_SELECTOR, loc.value)


def _key(k: str):
    from selenium.webdriver.common.keys import Keys
    parts = k.split("+")
    keys = [getattr(Keys, KEYS[p]) if p in KEYS else (getattr(Keys, p.upper()) if hasattr(Keys, p.upper()) and len(p) > 1 else p.lower())
            for p in parts]
    return keys


class SeleniumEngine(Engine):
    """Selenium WebDriver engine.

    WebDriver has no built-in video recording, so the engine captures a frame after every
    action (plus while waiting) and stitches them into video.webm with ffmpeg at the end.
    """
    name = "selenium"

    def start(self) -> None:
        from selenium import webdriver
        b = self.config.browser
        if b == "firefox":
            opts = webdriver.FirefoxOptions()
            if self.config.headless:
                opts.add_argument("-headless")
            self.driver = webdriver.Firefox(options=opts)
        elif b in ("edge", "msedge"):
            opts = webdriver.EdgeOptions()
            if self.config.headless:
                opts.add_argument("--headless=new")
            self.driver = webdriver.Edge(options=opts)
        else:
            opts = webdriver.ChromeOptions()
            if self.config.headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument(f"--window-size={self.config.viewport_width},{self.config.viewport_height}")
            if chrome_binary():
                opts.binary_location = chrome_binary()
            service = None
            if os.getenv("AIAUTO_CHROMEDRIVER"):
                from selenium.webdriver.chrome.service import Service
                service = Service(os.getenv("AIAUTO_CHROMEDRIVER"))
            self.driver = webdriver.Chrome(options=opts, service=service) if service else webdriver.Chrome(options=opts)
        self.driver.set_window_size(self.config.viewport_width, self.config.viewport_height)
        self.driver.set_page_load_timeout(max(30, self.config.timeout_ms // 1000 * 3))
        self.init_frames()

    def _grab(self, path: Path) -> None:
        self.driver.save_screenshot(str(path))

    def stop(self) -> str | None:
        self.frame()
        try:
            self.driver.quit()
        except Exception:
            pass
        return self.finish_frames()

    # page ----------------------------------------------------------------
    def goto(self, url):
        self.driver.get(url)
        self.frame()

    def back(self):
        self.driver.back(); self.frame()

    def refresh(self):
        self.driver.refresh(); self.frame()

    def url(self): return self.driver.current_url
    def title(self): return self.driver.title

    def page_text(self):
        return self.driver.execute_script("return document.body ? document.body.innerText : ''")

    def snapshot(self):
        self._follow_new_window()
        return self.driver.execute_script(SNAPSHOT_JS)

    def screenshot(self, path, full_page=False):
        self.driver.save_screenshot(str(path))

    def wait_for_load(self):
        end = time.time() + self.config.timeout_ms / 1000
        while time.time() < end:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    break
            except Exception:
                pass
            time.sleep(0.1)
        self._follow_new_window()

    def _follow_new_window(self):
        handles = self.driver.window_handles
        if handles and self.driver.current_window_handle != handles[-1]:
            self.driver.switch_to.window(handles[-1])

    def press(self, key, locator=None):
        from selenium.webdriver.common.action_chains import ActionChains
        keys = _key(key)
        if locator:
            self._el(locator).send_keys("".join(keys))  # modifiers stay held for the rest of the string
        else:
            ac = ActionChains(self.driver)
            for k in keys[:-1]:
                ac.key_down(k)
            ac.send_keys(keys[-1])
            for k in reversed(keys[:-1]):
                ac.key_up(k)
            ac.perform()
        self.frame()

    def scroll(self, direction, amount=600):
        js = {"top": "window.scrollTo(0,0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)",
              "up": f"window.scrollBy(0,-{amount})"}.get(direction, f"window.scrollBy(0,{amount})")
        self.driver.execute_script(js)
        self.frame()

    # elements ------------------------------------------------------------
    def _el(self, locator, clickable=False):
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        cond = EC.element_to_be_clickable if clickable else EC.presence_of_element_located
        return WebDriverWait(self.driver, self.config.timeout_ms / 1000).until(cond(_by(locator)))

    def exists(self, locator, timeout_ms):
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        try:
            WebDriverWait(self.driver, timeout_ms / 1000).until(EC.presence_of_element_located(_by(locator)))
            return True
        except Exception:
            return False

    def click(self, locator, button="left", double=False):
        from selenium.webdriver.common.action_chains import ActionChains
        el = self._el(locator, clickable=True)
        self.scroll_into_view(locator)
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

    def fill(self, locator, text, clear=True):
        el = self._el(locator)
        if clear:
            el.clear()
        el.send_keys(text)
        self.frame()

    def hover(self, locator):
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(self.driver).move_to_element(self._el(locator)).perform()
        self.frame()

    def select(self, locator, option):
        from selenium.webdriver.support.ui import Select
        s = Select(self._el(locator))
        try:
            s.select_by_visible_text(option)
        except Exception:
            s.select_by_value(option)
        self.frame()

    def set_checked(self, locator, checked):
        el = self._el(locator)
        if el.is_selected() != checked:
            try:
                el.click()
            except Exception:
                self.driver.execute_script("arguments[0].click()", el)
        self.frame()

    def text_of(self, locator):
        el = self._el(locator)
        if el.tag_name.lower() in ("input", "textarea", "select"):
            return el.get_attribute("value") or ""
        return el.text

    def scroll_into_view(self, locator):
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", self._el(locator))

    def upload(self, locator, path):
        self._el(locator).send_keys(os.path.abspath(path)); self.frame()

    def highlight(self, locator):
        try:
            self.driver.execute_script(
                "const el=arguments[0]; const p=el.style.outline; el.style.outline='3px solid #ff3b6b';"
                "el.style.outlineOffset='2px'; setTimeout(()=>{el.style.outline=p},900);", self._el(locator))
            self.frame()
        except Exception:
            pass
