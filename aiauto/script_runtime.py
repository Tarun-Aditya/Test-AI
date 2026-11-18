"""autopilot_runtime: the standalone runtime that generated test scripts use.

This one file is copied next to every generated test_playwright.py / test_selenium.py (and into exported
projects). It has no dependency on the AI tool: only playwright or selenium (+ imageio-ffmpeg for Selenium video).
It gives a plain script the same output as a run in the tool:

  * browser started from the recorded configuration (browser, headless, viewport, timeouts, saved session or
    Chrome profile, closed shadow roots opened), overridable with environment variables;
  * per step: the green highlight box (dashed when located, solid when done, red on failure), a screenshot,
    timing, pass/fail and the error; secrets masked in screenshots and text;
  * at the end: report.html (screenshots + video embedded), video.webm, summary.json and junit.xml in RESULTS_DIR.

No AI and no self-healing: a script either passes or fails.

Environment overrides: BROWSER, HEADLESS=1|0, NO_VIDEO=1, STEP_TIMEOUT (ms), RESULTS_DIR, STORAGE_STATE,
CHROME_PROFILE_DIR, CHROME_PROFILE_NAME, HIGHLIGHT=0, AIAUTO_CHROME_BINARY, AIAUTO_CHROMEDRIVER.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MASK = "••••••"

# ---------------------------------------------------------------- shared browser-side scripts
OPEN_SHADOW_JS = """(() => { const proto = Element.prototype; if (proto.attachShadow && !proto.attachShadow.__aiauto) {
  const orig = proto.attachShadow; const patched = function (init) { return orig.call(this, Object.assign({}, init, { mode: 'open' })); };
  patched.__aiauto = true; proto.attachShadow = patched; } })();"""

MASK_JS = """(el) => { el.style.setProperty('-webkit-text-security', 'disc', 'important');
  el.style.setProperty('text-security', 'disc', 'important'); }"""

BOX_CSS = (
    "[data-aiauto-box]{position:fixed!important;pointer-events:none!important;z-index:2147483647!important;box-sizing:border-box!important;"
    "border:3px solid #16a34a!important;border-radius:5px!important;box-shadow:0 0 0 3px rgba(22,163,74,.28)!important;"
    "background:rgba(22,163,74,.07)!important;margin:0!important;padding:0!important;transition:none!important;display:block!important}"
    "[data-aiauto-box=located]{border-style:dashed!important;background:transparent!important}"
    "[data-aiauto-box=failed]{border-color:#dc2626!important;box-shadow:0 0 0 3px rgba(220,38,38,.28)!important;background:rgba(220,38,38,.07)!important}"
    "[data-aiauto-box]::before{content:attr(data-label);position:absolute;left:-3px;bottom:100%;margin-bottom:3px;background:#16a34a;"
    "color:#fff;font:600 11px/18px system-ui,-apple-system,'Segoe UI',sans-serif;padding:0 7px;border-radius:4px;white-space:nowrap}"
    "[data-aiauto-box][data-below]::before{bottom:auto;top:100%;margin:3px 0 0}"
    "[data-aiauto-box=failed]::before{background:#dc2626}"
    "[data-aiauto-box][data-point]{border-radius:50%!important}"
)
HL_BOX_JS = """(el, o) => {
  const doc = el ? el.ownerDocument : document;
  if (!doc.getElementById('__aiauto_box_css')) { const st = doc.createElement('style'); st.id = '__aiauto_box_css';
    st.textContent = """ + json.dumps(BOX_CSS) + """; (doc.head || doc.documentElement).appendChild(st); }
  doc.querySelectorAll('[data-aiauto-box]').forEach(b => b.remove());
  let r;
  if (el) { const q = el.getBoundingClientRect(); if (!q.width && !q.height) return false; r = {x: q.left, y: q.top, w: q.width, h: q.height}; }
  else r = {x: o.x - 15, y: o.y - 15, w: 30, h: 30};
  const b = doc.createElement('div'), pad = 3;
  b.setAttribute('data-aiauto-box', o.state); b.setAttribute('aria-hidden', 'true'); b.setAttribute('data-label', o.label || '');
  if (r.y < 26) b.setAttribute('data-below', '1');
  if (!el) b.setAttribute('data-point', '1');
  b.style.cssText = 'left:' + (r.x - pad) + 'px;top:' + (r.y - pad) + 'px;width:' + (r.w + pad * 2) + 'px;height:' + (r.h + pad * 2) + 'px';
  doc.documentElement.appendChild(b);
  setTimeout(() => b.remove(), o.ttl || 20000);
  return true;
}"""
HL_CLEAR_JS = """(el) => { const docs = [el ? el.ownerDocument : document];
  for (const f of document.querySelectorAll('iframe')) { try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {} }
  for (const d of docs) d.querySelectorAll('[data-aiauto-box]').forEach(b => b.remove()); return true; }"""

VERBS = {"click": "clicked", "double_click": "double-clicked", "right_click": "right-clicked", "type": "typed",
         "append": "typed", "press": "key pressed", "hover": "hovered", "select": "selected", "check": "checked",
         "uncheck": "unchecked", "drag": "dragged", "upload": "uploaded", "scroll": "scrolled to", "wait": "appeared",
         "assert_visible": "visible", "assert_text": "text matches"}


def _env_bool(name, default):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "test").lower()).strip("-")[:50] or "test"


class StepSkipped(Exception):
    pass


class Run:
    """with Run(name, "playwright", steps, config) as r:  r.page / r.driver ... with r.step(...): ..."""

    def __init__(self, name, engine, steps, config=None, source="", secrets=(), data=None):
        c = dict(config or {})
        self.name, self.engine, self.titles, self.source = name, engine, list(steps), source
        self.browser_name = os.getenv("BROWSER") or c.get("browser") or ("chromium" if engine == "playwright" else "chrome")
        self.headless = _env_bool("HEADLESS", c.get("headless", False))
        self.video = not _env_bool("NO_VIDEO", not c.get("record_video", True))
        self.highlight = _env_bool("HIGHLIGHT", c.get("highlight", True))
        self.highlight_ms = int(c.get("highlight_ms", 300))
        self.timeout_ms = int(os.getenv("STEP_TIMEOUT") or c.get("timeout_ms") or 15000)
        self.viewport = (int(c.get("viewport_width", 1366)), int(c.get("viewport_height", 900)))
        self.secrets = sorted({str(s) for s in secrets if s and str(s) != MASK}, key=len, reverse=True)
        self.data = data or {}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = Path(os.getenv("RESULTS_DIR") or Path("results") / f"{_slug(name)}-{engine}-{stamp}").resolve()
        (self.dir / "shots").mkdir(parents=True, exist_ok=True)
        self.results, self.started = [], None
        self.passed = False
        self._frames, self._frame_mode, self._lock = [], False, threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def __enter__(self):
        self.started = time.time()
        try:
            (self._start_playwright if self.engine == "playwright" else self._start_selenium)()
        except Exception as e:      # browser/driver could not start: still leave a report explaining why
            self.results.append({"n": 0, "title": f"Start {self.engine} ({self.browser_name})", "status": "failed",
                                 "error": self._m(f"{type(e).__name__}: {str(e).strip()[:600]}"), "ms": 0})
            self.__exit__(type(e), e, None)
            raise SystemExit(1)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None and not isinstance(exc, StepSkipped) and (not self.results or self.results[-1]["status"] != "failed"):
            self.results.append({"n": len(self.titles) + 1, "title": "(script error)", "status": "failed",
                                 "error": self._m(f"{type(exc).__name__}: {exc}"), "ms": 0})
        done = {r["n"] for r in self.results}
        for n, title in enumerate(self.titles, 1):        # steps never reached
            if n not in done:
                self.results.append({"n": n, "title": title, "status": "skipped", "ms": 0})
        self.results.sort(key=lambda r: r["n"])
        self.passed = exc is None and all(r["status"] != "failed" for r in self.results)
        try:
            video = self._stop()
        except Exception as e:
            print(f"warning: could not close the browser cleanly: {e}", file=sys.stderr)
            video = None
        self._write(video)
        print(("PASSED" if self.passed else "FAILED") + f"  -> {self.dir / 'report.html'}")
        return True          # recorded; the caller checks r.passed / exits with r.exit_code

    @property
    def exit_code(self):
        return 0 if self.passed else 1

    # ------------------------------------------------------------ browsers
    def _start_playwright(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        bt = {"firefox": self._pw.firefox, "webkit": self._pw.webkit}.get(self.browser_name, self._pw.chromium)
        launch = {"headless": self.headless}
        if self.browser_name in ("chrome", "msedge"):
            launch["channel"] = self.browser_name
        elif bt is self._pw.chromium and os.getenv("AIAUTO_CHROME_BINARY"):
            launch["executable_path"] = os.environ["AIAUTO_CHROME_BINARY"]
        vp = {"width": self.viewport[0], "height": self.viewport[1]}
        opts = {"viewport": vp}
        if self.video:
            opts.update(record_video_dir=str(self.dir / "_video"), record_video_size=vp)

        def open_ctx(o):
            if os.getenv("CHROME_PROFILE_DIR"):
                args = [f"--profile-directory={os.environ['CHROME_PROFILE_NAME']}"] if os.getenv("CHROME_PROFILE_NAME") else []
                self._browser = None
                return bt.launch_persistent_context(os.environ["CHROME_PROFILE_DIR"], **launch, args=args, **o)
            self._browser = bt.launch(**launch)
            if os.getenv("STORAGE_STATE"):
                o = {**o, "storage_state": os.environ["STORAGE_STATE"]}
            return self._browser.new_context(**o)
        try:
            self.ctx = open_ctx(opts)
            self._page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
            self._native_video = self.video
        except Exception as e:
            if "ffmpeg" not in str(e).lower():
                raise
            try:
                (self._browser or self.ctx).close()
            except Exception:
                pass
            opts.pop("record_video_dir", None)
            opts.pop("record_video_size", None)
            self.ctx = open_ctx(opts)
            self._page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
            self._native_video, self._frame_mode = False, self.video     # frames + ffmpeg instead
        self.ctx.add_init_script(OPEN_SHADOW_JS)
        self.ctx.set_default_timeout(self.timeout_ms)
        self.ctx.on("page", lambda p: setattr(self, "_page", p))

    def _start_selenium(self):
        from selenium import webdriver
        b = self.browser_name
        if b == "firefox":
            o = webdriver.FirefoxOptions()
            if self.headless:
                o.add_argument("-headless")
            o.set_capability("unhandledPromptBehavior", "ignore")
            self.driver = webdriver.Firefox(options=o)
        else:
            edge = b in ("msedge", "edge")
            o = webdriver.EdgeOptions() if edge else webdriver.ChromeOptions()
            o.add_argument(f"--window-size={self.viewport[0]},{self.viewport[1]}")
            o.set_capability("unhandledPromptBehavior", "ignore")
            if self.headless:
                o.add_argument("--headless=new")
                o.add_argument("--no-sandbox")
            if os.getenv("AIAUTO_CHROME_BINARY") and not edge:
                o.binary_location = os.environ["AIAUTO_CHROME_BINARY"]
            if os.getenv("CHROME_PROFILE_DIR"):
                o.add_argument("--user-data-dir=" + os.environ["CHROME_PROFILE_DIR"])
                if os.getenv("CHROME_PROFILE_NAME"):
                    o.add_argument("--profile-directory=" + os.environ["CHROME_PROFILE_NAME"])
            service = None
            if os.getenv("AIAUTO_CHROMEDRIVER") and not edge:
                from selenium.webdriver.chrome.service import Service
                service = Service(os.environ["AIAUTO_CHROMEDRIVER"])
            cls = webdriver.Edge if edge else webdriver.Chrome
            self.driver = cls(options=o, service=service) if service else cls(options=o)
            try:
                self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": OPEN_SHADOW_JS})
                if os.getenv("STORAGE_STATE"):
                    state = json.loads(Path(os.environ["STORAGE_STATE"]).read_text())
                    self.driver.execute_cdp_cmd("Network.setCookies", {"cookies": [
                        {k: c[k] for k in ("name", "value", "domain", "path", "secure", "httpOnly") if k in c} for c in state.get("cookies", [])]})
            except Exception:
                pass
        self.driver.set_window_size(*self.viewport)
        if self.video:      # WebDriver has no recorder: ~4 frames/s in the background, serialised with commands
            self._frame_mode = True
            orig = self.driver.execute

            def locked(*a, **k):
                with self._lock:
                    return orig(*a, **k)
            self.driver.execute = locked
            self._stop_rec = threading.Event()
            self._rec = threading.Thread(target=self._record_loop, daemon=True)
            self._rec.start()

    @property
    def page(self):
        return self._page

    # ------------------------------------------------------------ video
    def _grab(self):
        if self.engine == "playwright":
            return self._page.screenshot(type="jpeg", quality=70), "jpg"
        try:
            data = self.driver.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": 70})["data"]
            return base64.b64decode(data), "jpg"
        except Exception:
            return self.driver.get_screenshot_as_png(), "png"

    def _frame(self):
        if not self._frame_mode or self.engine != "playwright":
            return
        try:
            img, ext = self._grab()
            p = self.dir / "_frames" / f"{len(self._frames):05d}.{ext}"
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(img)
            self._frames.append((time.time(), p))
        except Exception:
            pass

    def _record_loop(self):
        (self.dir / "_frames").mkdir(exist_ok=True)
        while not self._stop_rec.is_set():
            t0 = time.time()
            try:
                with self._lock:
                    img, ext = self._grab()
                p = self.dir / "_frames" / f"{len(self._frames):05d}.{ext}"
                p.write_bytes(img)
                self._frames.append((time.time(), p))
            except Exception:
                pass
            self._stop_rec.wait(max(0.0, 0.25 - (time.time() - t0)))

    def _render_frames(self):
        frames = [f for f in self._frames if f[1].exists()]
        if not frames:
            return None
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            print("note: no ffmpeg found (pip install imageio-ffmpeg) - video skipped", file=sys.stderr)
            return None
        lst = self.dir / "_frames" / "frames.txt"
        lines = []
        for i, (t, p) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 1.5
            lines.append(f"file '{p.name}'\nduration {max(0.05, min(nxt - t, 5)):.3f}")
        lines.append(f"file '{frames[-1][1].name}'")
        lst.write_text("\n".join(lines))
        out = self.dir / "video.webm"
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=12"
        for codec in (["-c:v", "libvpx-vp9", "-b:v", "1M", "-deadline", "realtime", "-cpu-used", "8"], ["-c:v", "libx264", "-pix_fmt", "yuv420p"]):
            if codec[1] == "libx264":
                out = self.dir / "video.mp4"
            try:
                subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf, *codec, str(out)],
                               check=True, cwd=lst.parent, timeout=600)
                return out.name
            except Exception:
                continue
        return None

    def _stop(self):
        video = None
        try:
            if self.engine == "playwright":
                self._frame()
                vid = self._page.video if getattr(self, "_native_video", False) else None
                self.ctx.close()
                if vid:
                    src = Path(vid.path())
                    if src.exists():
                        shutil.move(str(src), self.dir / "video.webm")
                        video = "video.webm"
                if self._browser:
                    self._browser.close()
                self._pw.stop()
            else:
                if getattr(self, "_rec", None):
                    self._stop_rec.set()
                    self._rec.join(timeout=5)
                self.driver.quit()
        except Exception as e:
            print(f"warning: closing the browser failed: {e}", file=sys.stderr)
        if self._frame_mode and video is None:
            video = self._render_frames()
        shutil.rmtree(self.dir / "_frames", ignore_errors=True)
        shutil.rmtree(self.dir / "_video", ignore_errors=True)
        return video

    # ------------------------------------------------------------ highlight / mask
    def _sel_find(self, spec):
        """Selenium: (by, value, frames, shadow) -> element or None, without waiting."""
        from selenium.webdriver.common.by import By
        by, value, frames, shadow = spec
        d = self.driver
        d.switch_to.default_content()
        for f in frames or ():
            d.switch_to.frame(d.find_element(By.CSS_SELECTOR, f))
        ctx = d
        for host in shadow or ():
            ctx = ctx.find_element(By.CSS_SELECTOR, host).shadow_root
        els = ctx.find_elements(by, value)
        return els[0] if els else None

    def _js_on(self, target, fn, arg=None, timeout_ms=800):
        if self.engine == "playwright":
            return target.first.evaluate(fn, arg, timeout=timeout_ms)
        el = self._sel_find(target)
        if el is None:
            return None
        try:
            return self.driver.execute_script(f"return ({fn})(arguments[0], arguments[1]);", el, arg)
        finally:
            self.driver.switch_to.default_content()

    def box(self, target, state, label):
        if not self.highlight or target is None:
            return False
        try:
            if isinstance(target, tuple) and target and target[0] == "point":
                o = {"state": state, "label": label, "x": target[1], "y": target[2]}
                js = f"return ({HL_BOX_JS})(null, {json.dumps(o)})"
                ok = self._page.evaluate("() => {" + js + "}") if self.engine == "playwright" else self.driver.execute_script(js)
            else:
                ok = self._js_on(target, HL_BOX_JS, {"state": state, "label": label})
            self._frame()
            return bool(ok)
        except Exception:
            return False

    def clear_box(self):
        try:
            js = f"return ({HL_CLEAR_JS})(null)"
            self._page.evaluate("() => {" + js + "}") if self.engine == "playwright" else self.driver.execute_script(js)
        except Exception:
            pass

    def mask(self, target):
        """Render a field's content as dots (sensitive values never show in screenshots / video)."""
        try:
            self._js_on(target, MASK_JS, timeout_ms=self.timeout_ms)
        except Exception:
            pass

    # ------------------------------------------------------------ steps
    def _m(self, text):
        if not isinstance(text, str):
            return text
        for s in sorted(set(self.secrets), key=len, reverse=True):
            if not s:
                continue
            if len(s) >= 4:
                text = text.replace(s, MASK)
            else:        # short secrets only as a whole token: don't mangle "127.0.0.1" for a secret "2"
                text = re.sub(rf"(?<![\w.]){re.escape(s)}(?![\w.])", MASK, text)
        return text

    def skip(self, n, title, reason):
        self.results.append({"n": n, "title": self._m(title), "status": "skipped", "error": reason, "ms": 0})

    @contextmanager
    def step(self, n, title, action="", target=None, value=None, locator="", optional=False):
        """Wraps one step: located box -> action -> done box -> screenshot; failure -> red box + screenshot."""
        verb = action.replace("_", " ") or "step"
        rec = {"n": n, "title": self._m(title), "action": action, "value": self._m(value), "locator": locator, "status": "passed"}
        t0 = time.time()
        if target is not None and self.box(target, "located", f"Step {n} · {verb}") and (self.video or not self.headless):
            time.sleep(self.highlight_ms / 1000)
        try:
            yield
            rec["status"] = "passed"
            if target is not None:
                self.box(target, "done", f"✓ Step {n} · {VERBS.get(action, 'done')}")
        except Exception as e:
            msg = self._m(f"{type(e).__name__}: {str(e).strip().splitlines()[0] if str(e).strip() else ''}")[:600]
            if optional:          # e.g. "Click Accept cookies if shown": absent is fine
                rec["status"], rec["error"] = "skipped", "optional step skipped: " + msg
            else:
                rec["status"], rec["error"] = "failed", msg
                rec["trace"] = self._m(traceback.format_exc()[-2000:])
                if target is not None:
                    self.box(target, "failed", f"✗ Step {n} · {verb} failed")
                raise
        finally:
            rec["ms"] = int((time.time() - t0) * 1000)
            try:
                shot = self.dir / "shots" / f"step_{n:02d}.png"
                if self.engine == "playwright":
                    self._page.screenshot(path=str(shot))
                else:
                    self.driver.switch_to.default_content()
                    self.driver.save_screenshot(str(shot))
                rec["shot"] = f"shots/{shot.name}"
            except Exception:
                pass
            self.clear_box()
            try:
                rec["url"] = self._m(self._page.url if self.engine == "playwright" else self.driver.current_url)
            except Exception:
                pass
            self.results.append(rec)

    # ------------------------------------------------------------ outputs
    def _write(self, video):
        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        dur = int((time.time() - (self.started or time.time())) * 1000)
        summary = {"name": self.name, "engine": self.engine, "browser": self.browser_name, "status": "passed" if self.passed else "failed",
                   "started_at": datetime.fromtimestamp(self.started or time.time(), timezone.utc).isoformat(timespec="seconds"),
                   "finished_at": finished, "duration_ms": dur, "video": video,
                   "steps": [{k: v for k, v in r.items() if k != "trace"} for r in self.results]}
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2))
        self._junit(summary)
        (self.dir / "report.html").write_text(self._report(summary, video), encoding="utf-8")

    def _junit(self, s):
        e = lambda x: html.escape(str(x or ""), quote=True)
        cases = []
        for r in s["steps"]:
            body = ""
            if r["status"] == "failed":
                body = f'<failure message="{e(r.get("error"))}">{e(r.get("error"))}</failure>'
            elif r["status"] == "skipped":
                body = "<skipped/>"
            cases.append(f'  <testcase classname="{e(s["name"])}" name="{e(str(r["n"]) + ". " + r["title"])}" time="{r.get("ms", 0) / 1000:.3f}">{body}</testcase>')
        fails = sum(1 for r in s["steps"] if r["status"] == "failed")
        skips = sum(1 for r in s["steps"] if r["status"] == "skipped")
        (self.dir / "junit.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="{e(s["name"])} ({e(s["engine"])}/{e(s["browser"])})" tests="{len(s["steps"])}" '
            f'failures="{fails}" skipped="{skips}" time="{s["duration_ms"] / 1000:.3f}">\n' + "\n".join(cases) + "\n</testsuite>\n")

    def _report(self, s, video):
        e = lambda x: html.escape(str(x if x is not None else ""))
        b64 = lambda p: base64.b64encode((self.dir / p).read_bytes()).decode()
        vid = ""
        if video and (self.dir / video).exists():
            src = f"data:video/{'webm' if video.endswith('webm') else 'mp4'};base64,{b64(video)}" if (self.dir / video).stat().st_size < 25e6 else e(video)
            vid = f'<section class="card"><h2>Recording</h2><video controls preload="metadata" src="{src}"></video></section>'
        rows = []
        for r in s["steps"]:
            img = f'<a href="#" onclick="zoom(this);return false"><img loading="lazy" src="data:image/png;base64,{b64(r["shot"])}" alt=""></a>' \
                if r.get("shot") and (self.dir / r["shot"]).exists() else ""
            rows.append(f"""<article class="step {e(r['status'])}"><header><span class="num">{r['n']}</span><span class="st">{e(r['status'])}</span>
<span class="raw">{e(r['title'])}</span><span class="dur">{r.get('ms', 0)} ms</span></header><div class="body"><div>
{f'<div class="kv"><span>action</span><b>{e(r.get("action"))}</b></div>' if r.get('action') else ''}
{f'<div class="kv"><span>value</span><code>{e(r.get("value"))}</code></div>' if r.get('value') else ''}
{f'<div class="kv"><span>locator</span><code>{e(r.get("locator"))}</code></div>' if r.get('locator') else ''}
{f'<div class="kv"><span>url</span><code>{e(r.get("url"))}</code></div>' if r.get('url') else ''}
{f'<pre class="err">{e(r.get("error"))}</pre>' if r.get('error') else ''}</div>{img}</div></article>""")
        n = len(s["steps"]); p = sum(1 for r in s["steps"] if r["status"] == "passed"); f = sum(1 for r in s["steps"] if r["status"] == "failed")
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(s['name'])} – {e(s['status'])}</title><style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1c2230;--mut:#677085;--line:#e3e6ec;--ok:#1a7f4b;--bad:#c62f3c;--skip:#8a8f9c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11141a;--card:#1a1e27;--fg:#e6e9ef;--mut:#9aa3b5;--line:#2b303c;--ok:#3ccf87;--bad:#ff6b78}}}}
*{{box-sizing:border-box}}body{{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:1150px;margin:0 auto;padding:24px 16px 64px}}h1{{margin:0 0 4px;font-size:22px}}h2{{font-size:15px;margin:0 0 12px}}
.sub{{color:var(--mut)}}.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}}.stat{{flex:1;min-width:110px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.stat b{{display:block;font-size:22px}}.badge{{display:inline-block;padding:2px 10px;border-radius:99px;font-weight:600;color:#fff;font-size:14px}}
.badge.passed{{background:var(--ok)}}.badge.failed{{background:var(--bad)}}video{{width:100%;border-radius:8px;background:#000}}
.step{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--skip);border-radius:10px;margin:10px 0;overflow:hidden}}
.step.passed{{border-left-color:var(--ok)}}.step.failed{{border-left-color:var(--bad)}}
.step header{{display:flex;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}}
.num{{font-weight:700;color:var(--mut);min-width:22px}}.st{{text-transform:uppercase;font-size:11px;font-weight:700}}
.passed .st{{color:var(--ok)}}.failed .st{{color:var(--bad)}}.skipped .st{{color:var(--skip)}}.raw{{flex:1;min-width:200px}}.dur{{color:var(--mut);font-size:12px}}
.body{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:16px;padding:14px}}@media (max-width:760px){{.body{{grid-template-columns:1fr}}}}
.kv{{display:flex;gap:8px;margin:3px 0;word-break:break-all}}.kv span{{color:var(--mut);min-width:64px}}
code{{font:12px ui-monospace,Menlo,Consolas,monospace;background:var(--bg);padding:1px 5px;border-radius:4px}}
.err{{white-space:pre-wrap;color:var(--bad);font-size:12px}}img{{width:100%;border:1px solid var(--line);border-radius:6px;cursor:zoom-in}}
#lb{{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;padding:16px}}#lb img{{max-width:100%;max-height:100%;width:auto}}
pre.src{{white-space:pre-wrap;font-size:12px}}</style></head><body><main>
<h1>{e(s['name'])} <span class="badge {e(s['status'])}">{e(s['status']).upper()}</span></h1>
<div class="sub">Generated script · engine <b>{e(s['engine'])}</b> · browser <b>{e(s['browser'])}</b> · {e(s['started_at'])}</div>
<div class="stats"><div class="stat"><span class="sub">Steps</span><b>{n}</b></div><div class="stat"><span class="sub">Passed</span><b style="color:var(--ok)">{p}</b></div>
<div class="stat"><span class="sub">Failed</span><b style="color:var(--bad)">{f}</b></div><div class="stat"><span class="sub">Skipped</span><b>{n - p - f}</b></div>
<div class="stat"><span class="sub">Duration</span><b>{s['duration_ms'] / 1000:.1f}s</b></div></div>
{vid}<section><h2 style="margin-top:24px">Steps</h2>{''.join(rows)}</section>
{f'<section class="card"><h2>Instructions</h2><pre class="src">{e(self.source)}</pre></section>' if self.source else ''}
</main><div id="lb" onclick="this.style.display='none'"><img alt=""></div>
<script>function zoom(a){{const lb=document.getElementById('lb');lb.querySelector('img').src=a.querySelector('img').src;lb.style.display='flex'}}</script>
</body></html>"""
