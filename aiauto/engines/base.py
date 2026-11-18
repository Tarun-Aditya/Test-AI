"""Engine interface shared by Playwright and Selenium."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Locator, RunConfig

# Types whose value is set via JS in both engines (consistent, locale-independent behaviour).
JS_VALUE_TYPES = {"range", "color", "date", "time", "datetime-local", "month", "week"}

SET_VALUE_JS = """(el, v) => { const proto = Object.getPrototypeOf(el);
  const setter = Object.getOwnPropertyDescriptor(proto, 'value'); setter ? setter.set.call(el, v) : (el.value = v);
  el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }"""

INFO_JS = """(el) => ({tag: el.tagName.toLowerCase(), type: (el.getAttribute('type')||'').toLowerCase(),
  editable: !!el.isContentEditable, draggable: el.getAttribute('draggable') === 'true',
  role: el.getAttribute('role') || ''})"""

MASK_JS = """(el) => { el.style.setProperty('-webkit-text-security', 'disc', 'important');
  el.style.setProperty('text-security', 'disc', 'important'); }"""

HIGHLIGHT_JS = """(el) => { if (!el) return; const prev = el.style.outline, off = el.style.outlineOffset;
  el.style.outline = '3px solid #ff3b6b'; el.style.outlineOffset = '2px';
  setTimeout(() => { el.style.outline = prev; el.style.outlineOffset = off; }, 900); }"""

DND_JS = """(src, dst) => { const dt = new DataTransfer();
  const fire = (el, type) => el.dispatchEvent(new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: dt}));
  fire(src, 'dragstart'); fire(dst, 'dragenter'); fire(dst, 'dragover'); fire(dst, 'drop'); fire(src, 'dragend'); }"""


def localstorage_init_script(state: dict | None) -> str | None:
    """JS that seeds localStorage for each origin of a saved session (used where the engine has no
    native storage-state support: Selenium, and Playwright persistent profiles)."""
    origins = {o["origin"]: {i["name"]: i["value"] for i in o.get("localStorage", [])}
               for o in (state or {}).get("origins", []) if o.get("localStorage")}
    if not origins:
        return None
    return ("(() => { const data = " + json.dumps(origins) + "; const o = data[location.origin]; if (!o) return;"
            " for (const [k, v] of Object.entries(o)) { try { if (localStorage.getItem(k) === null) localStorage.setItem(k, v); } catch (e) {} } })();")


class Engine(ABC):
    name = "base"

    def __init__(self, config: RunConfig, run_dir: Path):
        self.config = config
        self.run_dir = run_dir
        self.last_dialog: str | None = None
        self.dialog_policy = config.dialogs
        self.on_live = None            # callback(mime, base64) for the live view in the web UI
        self._last_live = 0.0

    # lifecycle -------------------------------------------------------------
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> str | None:
        """Close the browser; return path to the recorded video (relative to run_dir) if any."""

    def storage_state(self) -> dict | None:
        """Cookies + localStorage in Playwright's storage_state format (portable across engines)."""
        return None

    # frame-based video -------------------------------------------------------
    def init_frames(self) -> None:
        self._frames: list[tuple[float, Path]] = []
        self._frame_dir = self.run_dir / "_frames"
        self._frame_mode = bool(self.config.record_video)
        if self._frame_mode:
            self._frame_dir.mkdir(parents=True, exist_ok=True)

    def _grab(self, path: Path) -> None:
        self.screenshot(path)

    def frame(self) -> None:
        self.live_tick()
        if not getattr(self, "_frame_mode", False) or getattr(self, "_continuous", False):
            return
        self._add_frame()

    # live view -------------------------------------------------------------------
    def live_image(self) -> tuple[str, bytes] | None:
        """A small, fast screenshot for the live view (engine-specific)."""
        return None

    def live_tick(self, force: bool = False) -> None:
        if not self.on_live or not self.config.live_view:
            return
        now = time.time()
        if not force and now - self._last_live < 1.0 / max(0.2, self.config.live_fps):
            return
        self._last_live = now
        try:
            img = self.live_image()
            if img:
                import base64
                self.on_live(img[0], base64.b64encode(img[1]).decode())
        except Exception:
            pass

    def _add_frame(self) -> None:
        try:
            p = self._frame_dir / f"{len(self._frames):05d}.png"
            self._grab(p)
            self._frames.append((time.time(), p))
        except Exception:
            pass

    def _render_video(self) -> str | None:
        frames = [f for f in self._frames if f[1].exists()]
        if not frames:
            return None
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        lst = self._frame_dir / "frames.txt"
        lines = []
        for i, (t, p) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 1.5
            lines.append(f"file '{p.name}'\nduration {max(0.05, min(nxt - t, 5)):.3f}")
        lines.append(f"file '{frames[-1][1].name}'")
        lst.write_text("\n".join(lines))
        out = self.run_dir / "video.webm"
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=12"
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
               "-c:v", "libvpx-vp9", "-b:v", "1M", "-deadline", "realtime", "-cpu-used", "8", str(out)]
        try:
            subprocess.run(cmd, check=True, cwd=self._frame_dir, timeout=600)
            return out.name
        except Exception:
            out = self.run_dir / "video.mp4"
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
            try:
                subprocess.run(cmd, check=True, cwd=self._frame_dir, timeout=600)
                return out.name
            except Exception:
                return None

    def finish_frames(self) -> str | None:
        video = self._render_video() if getattr(self, "_frame_mode", False) else None
        shutil.rmtree(self.run_dir / "_frames", ignore_errors=True)
        return video

    # page-level ------------------------------------------------------------
    @abstractmethod
    def goto(self, url: str) -> None: ...
    @abstractmethod
    def back(self) -> None: ...
    @abstractmethod
    def refresh(self) -> None: ...
    @abstractmethod
    def url(self) -> str: ...
    @abstractmethod
    def title(self) -> str: ...
    @abstractmethod
    def page_text(self) -> str:
        """Visible text of the page, including open shadow roots and same-origin iframes."""
    @abstractmethod
    def snapshot(self, words: list[str] | None = None) -> list[dict]:
        """Candidate elements for the current page (engine's best source); `words` lets big pages pre-filter."""

    def snapshot_dom(self, words: list[str] | None = None) -> list[dict]:
        """Fallback candidates from the DOM walk (SNAPSHOT_JS). Same as snapshot() unless overridden."""
        return self.snapshot(words)

    @abstractmethod
    def describe(self, cand: dict) -> dict | None:
        """Full info + unique locators (most robust first) for ONE chosen candidate."""
    @abstractmethod
    def screenshot(self, path: Path, full_page: bool = False) -> None: ...
    @abstractmethod
    def press(self, key: str, locator: Locator | None = None) -> None: ...
    @abstractmethod
    def scroll(self, direction: str, amount: int = 600) -> None: ...
    @abstractmethod
    def wait_for_load(self) -> None: ...
    @abstractmethod
    def switch_tab(self, which: str) -> None: ...

    def eval_js(self, script: str):
        """Run a script body (may `return`) in the top-level page."""
        raise NotImplementedError

    # actions at a screen position (elements drawn on a canvas, found visually)
    def click_point(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        raise NotImplementedError

    def hover_point(self, x: int, y: int) -> None:
        raise NotImplementedError

    def type_text(self, text: str) -> None:
        raise NotImplementedError

    def viewport(self) -> tuple[int, int]:
        return self.config.viewport_width, self.config.viewport_height

    def after_action(self) -> None:
        """Hook for engines that must handle dialogs explicitly (Selenium)."""

    # element-level (all take an engine-agnostic Locator, mapped to native APIs) -------------
    @abstractmethod
    def count(self, locator: Locator) -> int: ...
    @abstractmethod
    def marker(self, locator: Locator) -> str | None:
        """The snapshot marker (__aiautoId) of the element the locator points at."""
    @abstractmethod
    def info(self, locator: Locator) -> dict: ...
    @abstractmethod
    def click(self, locator: Locator, button: str = "left", double: bool = False) -> None: ...
    @abstractmethod
    def fill(self, locator: Locator, text: str, clear: bool = True) -> None: ...
    @abstractmethod
    def hover(self, locator: Locator) -> None: ...
    @abstractmethod
    def select(self, locator: Locator, options: list[str]) -> None: ...
    @abstractmethod
    def set_checked(self, locator: Locator, checked: bool) -> None: ...
    @abstractmethod
    def text_of(self, locator: Locator) -> str: ...
    @abstractmethod
    def scroll_into_view(self, locator: Locator) -> None: ...
    @abstractmethod
    def upload(self, locator: Locator, path: str) -> None: ...
    @abstractmethod
    def drag(self, src: Locator, dst: Locator) -> None: ...
    @abstractmethod
    def run_js_on(self, locator: Locator, fn: str, arg=None): ...

    def is_visible(self, locator: Locator) -> bool:
        try:
            return bool(self.run_js_on(locator, "(el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }"))
        except Exception:
            return False

    def exists(self, locator: Locator, timeout_ms: int) -> bool:
        end = time.time() + timeout_ms / 1000
        while True:
            try:
                if self.count(locator) >= 1:
                    return True
            except Exception:
                pass
            if time.time() >= end:
                return False
            time.sleep(0.25)

    def highlight(self, locator: Locator) -> None:
        try:
            self.run_js_on(locator, HIGHLIGHT_JS)
            self.frame()
        except Exception:
            pass

    def mask(self, locator: Locator) -> None:
        """Render the field's content as dots so secrets don't show in screenshots/video."""
        try:
            self.run_js_on(locator, MASK_JS)
        except Exception:
            pass


def chrome_binary() -> str | None:
    """Optional override for the browser binary (AIAUTO_CHROME_BINARY)."""
    p = os.getenv("AIAUTO_CHROME_BINARY")
    return p if p and os.path.exists(p) else None


def make_engine(config: RunConfig, run_dir: Path) -> Engine:
    if config.engine == "selenium":
        from .selenium_engine import SeleniumEngine
        return SeleniumEngine(config, run_dir)
    from .playwright_engine import PlaywrightEngine
    return PlaywrightEngine(config, run_dir)


# Injected before any page script: closed shadow roots are created open, so locators (and Playwright's
# snapshot) can see inside them. Applies to every frame of every page in the session.
OPEN_SHADOW_JS = """(() => { const proto = Element.prototype; if (proto.attachShadow && !proto.attachShadow.__aiauto) {
  const orig = proto.attachShadow; const patched = function (init) { return orig.call(this, Object.assign({}, init, { mode: 'open' })); };
  patched.__aiauto = true; proto.attachShadow = patched; } })();"""

# One scroll step while searching for an element that isn't rendered yet: the page first, then the largest
# scrollable container that isn't at its end. Returns what scrolled, or null when everything is at the end.
SCROLL_MORE_JS = """const doc = document.scrollingElement || document.documentElement;
if (doc.scrollTop + innerHeight < doc.scrollHeight - 4) { window.scrollBy(0, Math.round(innerHeight * 0.8)); return 'page'; }
const boxes = Array.from(document.querySelectorAll('*')).filter(e => e.clientHeight > 40 && e.scrollHeight > e.clientHeight + 20
  && /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollTop + e.clientHeight < e.scrollHeight - 4);
boxes.sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth);
if (boxes.length) { const e = boxes[0]; e.scrollTop += Math.round(e.clientHeight * 0.8); e.dispatchEvent(new Event('scroll')); return 'container'; }
return null;"""

# Scrollable containers (virtualised lists, panes): pick the largest one and remember it as window.__aiautoBox.
BOX_JS = """const boxes = Array.from(document.querySelectorAll('*')).filter(e => e.clientHeight > 40 && e.scrollHeight > e.clientHeight + 20
  && /(auto|scroll)/.test(getComputedStyle(e).overflowY));
boxes.sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth);
window.__aiautoBox = boxes[0] || null;
return boxes[0] ? {max: boxes[0].scrollHeight - boxes[0].clientHeight, step: boxes[0].clientHeight} : null;"""

# Set the box's scroll position, return the numbers visible in it (to binary-search an ordered list).
BOX_SEEK_JS = """const b = window.__aiautoBox; if (!b) return null; b.scrollTop = arguments[0]; b.dispatchEvent(new Event('scroll'));
return true;"""
BOX_NUMS_JS = """const b = window.__aiautoBox; if (!b) return null; const r = b.getBoundingClientRect();
const nums = []; const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
for (let n = walker.nextNode(); n; n = walker.nextNode()) { const p = n.parentElement; if (!p) continue;
  const q = p.getBoundingClientRect(); if (q.bottom < r.top || q.top > r.bottom || q.height === 0) continue;
  for (const m of (n.nodeValue.match(/\\d+/g) || [])) nums.push(Number(m)); }
return nums;"""

PAGE_TEXT_JS = """() => { const parts = [];
  const walk = (root) => { try { if (root.body) parts.push(root.body.innerText); } catch (e) {}
    const els = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of els) { if (el.shadowRoot) { parts.push(el.shadowRoot.textContent || ''); walk(el.shadowRoot); }
      if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') { try { if (el.contentDocument) walk(el.contentDocument); } catch (e) {} } } };
  walk(document); return parts.join('\\n'); }"""
