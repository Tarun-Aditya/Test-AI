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

Environment overrides: BROWSER, REMOTE_URL + REMOTE_CAPS (grid / cloud browser), HEADLESS=1|0, NO_VIDEO=1, STEP_TIMEOUT (ms), RESULTS_DIR, STORAGE_STATE,
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
for _stream in (sys.stdout, sys.stderr):          # a console that is not UTF-8 (Windows) must not crash a script over a character in a step title
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MASK = "••••••"
# Safari's WebDriver stalls every command (screenshots, even reading the alert) while a native alert/confirm/prompt
# is open, so on Safari the page's dialogs are answered in the page instead: recorded, then answered per the policy.
DIALOG_SHIM_JS = """const acc = arguments[0]; window.__aiautoAccept = acc;
if (!window.__aiautoShim) { window.__aiautoShim = 1; window.__aiautoDialogs = [];
  window.alert = function (m) { window.__aiautoDialogs.push(String(m)); };
  window.confirm = function (m) { window.__aiautoDialogs.push(String(m)); return !!window.__aiautoAccept; };
  window.prompt = function (m, d) { window.__aiautoDialogs.push(String(m)); return window.__aiautoAccept ? (d == null ? '' : String(d)) : null; }; }
const out = window.__aiautoDialogs.splice(0); return out.length ? out[out.length - 1] : null;"""


_HELD = [None]   # the hover a HOVER step holds (see Run.hover / rehover)


def choose_option(page, loc, wanted):
    """Pick option(s) by visible text the way the runner does: a native <select> is set, a text field is filled, and a custom
    dropdown is opened and each option clicked by its text."""
    el = loc.first
    info = el.evaluate("e => ({tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase()})")
    opts = [o.strip() for o in str(wanted).split("|") if o.strip()]
    if info["tag"] == "select":
        try:
            loc.select_option(label=opts)
        except Exception:
            loc.select_option(value=opts)
    elif info["tag"] in ("input", "textarea") and info["type"] not in ("checkbox", "radio"):
        loc.fill(opts[0] if opts else "")
    else:
        for o in opts:
            el.click()
            page.wait_for_timeout(300)
            cand = page.get_by_role("option", name=o, exact=True)
            if not cand.count():
                cand = page.get_by_text(o, exact=True)
            cand.first.click()


def rehover():
    """Hover again over the element a hover step holds (used while waiting for text it reveals)."""
    if _HELD[0]:
        try:
            _HELD[0]()
        except Exception:
            pass


HOVER_PASSIVE = ("assert_text", "assert_no_text", "assert_visible", "assert_url", "assert_title", "wait", "screenshot")

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
         "assert_visible": "visible", "assert_text": "text matches", "assert_no_text": "text absent"}


def _env_bool(name, default):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "test").lower()).strip("-")[:50] or "test"


def selenium_browser(name):
    """Which browser binary and driver Selenium uses for a browser name: (kind, binary or None, driver or None).
    chromium -> AIAUTO_CHROME_BINARY (Playwright's Chromium) + AIAUTO_CHROMEDRIVER; chrome -> Google Chrome (falls
    back to that Chromium when Chrome is not installed); msedge -> Edge + AIAUTO_EDGEDRIVER; firefox -> Firefox
    (AIAUTO_FIREFOX_BINARY) + AIAUTO_GECKODRIVER. Unset drivers are resolved by Selenium Manager."""
    name = (name or "chrome").lower()
    if name == "safari":
        if sys.platform != "darwin":
            raise RuntimeError("Safari runs only on a Mac (for its engine elsewhere use Playwright WebKit)")
        return "safari", None, None
    if name == "webkit":
        if sys.platform == "darwin":
            return "safari", None, None      # Selenium's WebKit on a Mac is Safari
        raise RuntimeError("WebKit runs only with Playwright here (Selenium can drive Safari only on a Mac)")
    if name == "firefox":
        return "firefox", os.getenv("AIAUTO_FIREFOX_BINARY"), os.getenv("AIAUTO_GECKODRIVER")
    if name in ("msedge", "edge"):
        return "edge", os.getenv("AIAUTO_EDGE_BINARY"), os.getenv("AIAUTO_EDGEDRIVER")
    chromium = os.getenv("AIAUTO_CHROME_BINARY")
    chromium = chromium if chromium and os.path.exists(chromium) else None
    real = os.getenv("AIAUTO_GOOGLE_CHROME") or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if name == "chromium" or (name == "chrome" and chromium and not real):
        return "chrome", chromium, os.getenv("AIAUTO_CHROMEDRIVER")
    return "chrome", real if os.getenv("AIAUTO_GOOGLE_CHROME") else None, os.getenv("AIAUTO_GOOGLE_CHROMEDRIVER")


_SM_CACHE = {}


def _matching_driver(kind, binary):
    """Selenium Manager's driver for this browser, ignoring any (possibly outdated) driver on PATH."""
    key = (kind, binary)
    if key not in _SM_CACHE:
        try:
            from selenium.webdriver.common.selenium_manager import SeleniumManager
            args = ["--browser", {"chrome": "chrome", "edge": "MicrosoftEdge", "firefox": "firefox"}[kind], "--skip-driver-in-path"]
            if binary:
                args += ["--browser-path", binary]
            _SM_CACHE[key] = SeleniumManager().binary_paths(args).get("driver_path")
        except Exception:
            _SM_CACHE[key] = None        # let Selenium resolve it the usual way
    return _SM_CACHE[key]


def selenium_service(kind, driver, binary=None):
    if kind == "safari":
        return None                      # safaridriver ships with macOS
    driver = driver or _matching_driver(kind, binary)
    if not driver:
        return None
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.firefox.service import Service as FirefoxService
    return {"chrome": ChromeService, "edge": EdgeService, "firefox": FirefoxService}[kind](driver)


FRAME_TEXT_JS = """() => { const p = []; const w = (r) => { try { if (r.body) p.push(r.body.innerText); } catch (e) {}
  for (const el of (r.querySelectorAll ? r.querySelectorAll('*') : [])) if (el.shadowRoot) { p.push(el.shadowRoot.textContent || ''); w(el.shadowRoot); } };
  w(document); return p.join('\\n'); }"""


# ------------------------------------------------------------------------------------------------ extras
# Used by generated scripts and by the tool's own runner (one implementation, so a script behaves like the run it came from):
# one-time passwords, codes read from an inbox, API calls, screenshot comparison, network mocks, accessibility scan.
def totp(secret, digits=6, period=30, at=None):
    """The current one-time password (RFC 6238, as Google Authenticator / Authy / Microsoft Authenticator show it)."""
    import base64 as _b64, hashlib, hmac, struct
    key = re.sub(r"[\s-]", "", str(secret)).upper()
    key = _b64.b32decode(key + "=" * (-len(key) % 8))
    counter = int((time.time() if at is None else at) // period)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)).zfill(digits)


def code_from(url, timeout=30):
    """A verification code (4-8 digits) read from an HTTP inbox, waiting for it to arrive: a mail catcher (Mailpit, MailHog),
    a webhook inbox, an SMS gateway's 'last message' URL. The newest match wins."""
    import urllib.request
    pats = [r"(?:code|otp|pin|passcode|verification)\D{0,40}?(\d{4,8})\b", r"\b(\d{6})\b", r"\b(\d{4,8})\b"]
    end, last = time.time() + timeout, ""
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                last = re.sub(r"<[^>]+>", " ", r.read().decode("utf-8", "replace")).replace("\\n", " ")
            for pat in pats:
                m = re.search(pat, last, re.I)
                if m:
                    return m.group(1)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if time.time() > end:
            raise TimeoutError(f"no verification code arrived at that address within {timeout}s (last answer: {last[:120]!r})")
        time.sleep(1.5)


def split_api_target(target):
    """"POST /api/orders" -> ("POST", "/api/orders"); a bare address is a GET."""
    m = re.match(r"^\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S.*)$", target or "", re.I)
    return (m.group(1).upper(), m.group(2).strip()) if m else ("GET", (target or "").strip())


def api_request(method, url, headers=None, body=None, timeout=30):
    """One HTTP call with the standard library. Returns (status, headers, text); an HTTP error status is an answer, not an exception."""
    import urllib.error, urllib.request
    data = None
    h = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, (bytes, bytearray)) else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
        if not isinstance(body, (str, bytes, bytearray)):
            h.setdefault("Content-Type", "application/json")
    h.setdefault("User-Agent", "autopilot")
    req = urllib.request.Request(url, data=data, headers=h, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def _dig(obj, path):
    cur = obj
    for tok in re.findall(r"[^.\[\]]+", path):
        cur = cur[int(tok)] if isinstance(cur, list) else cur[tok]
    return cur


def check_api(status, text, spec):
    """Compare an API answer with what the step expects. Returns (problem or None, values to save for later steps)."""
    spec = spec or {}
    want = spec.get("expect_status")
    if want is not None and int(status) != int(want):
        return f"expected status {want}, got {status}: {text[:160]!r}", {}
    if want is None and status >= 400:
        return f"the call answered {status}: {text[:160]!r}", {}
    if spec.get("expect_contains") and spec["expect_contains"] not in text:
        return f"expected the answer to contain {spec['expect_contains']!r}; got {text[:160]!r}", {}
    saved = {}
    if spec.get("expect_json") or spec.get("save"):
        try:
            doc = json.loads(text)
        except ValueError:
            return f"the answer is not JSON: {text[:120]!r}", {}
        for path, expected in (spec.get("expect_json") or {}).items():
            try:
                got = _dig(doc, path)
            except (KeyError, IndexError, ValueError, TypeError):
                return f"the answer has no '{path}'", {}
            if got != expected:
                return f"expected {path} = {expected!r}, got {got!r}", {}
        for name, path in (spec.get("save") or {}).items():
            try:
                saved[name] = str(_dig(doc, path))
            except (KeyError, IndexError, ValueError, TypeError):
                return f"cannot save '{name}': the answer has no '{path}'", {}
    return None, saved


def visual_compare(actual_png, baseline, tolerance=0.001, update=False, threshold=24):
    """Compare a screenshot with its baseline file. Returns (state, message, changed_ratio, diff_png).
    state: created (no baseline yet, or approved now) | same | different. A pixel counts as changed when a colour channel
    moves by more than `threshold`; `tolerance` is the share of changed pixels that is still accepted (0.001 = 0.1%)."""
    baseline = Path(baseline)
    if update or not baseline.exists():
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(actual_png)
        return "created", ("baseline replaced" if update else "no baseline yet: this screenshot is now the baseline"), 0.0, None
    old = baseline.read_bytes()
    try:
        from PIL import Image, ImageChops
        import io
        a, b = Image.open(io.BytesIO(old)).convert("RGB"), Image.open(io.BytesIO(actual_png)).convert("RGB")
    except ImportError:
        same = old == actual_png
        return ("same", "identical", 0.0, None) if same else ("different", "the screenshots differ (install Pillow for a pixel comparison)", 1.0, None)
    if a.size != b.size:
        return "different", f"the page is {b.size[0]}x{b.size[1]} but the baseline is {a.size[0]}x{a.size[1]}", 1.0, None
    d = ImageChops.difference(a, b)
    mask = d.convert("L").point(lambda p: 255 if p > 0 else 0)
    strong = Image.merge("L", [c.point(lambda p: 255 if p > threshold else 0) for c in d.split()][:1])
    for c in d.split()[1:]:
        strong = ImageChops.lighter(strong, c.point(lambda p: 255 if p > threshold else 0))
    changed = sum(strong.histogram()[255:])
    ratio = changed / float(a.size[0] * a.size[1])
    if ratio <= tolerance:
        return "same", ("identical" if not changed else f"{ratio * 100:.3f}% of pixels differ (accepted up to {tolerance * 100:.3f}%)"), ratio, None
    red = Image.new("RGB", a.size, (230, 40, 60))
    out = Image.composite(red, Image.blend(a, Image.new("RGB", a.size, (255, 255, 255)), 0.6), strong)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return "different", f"{ratio * 100:.2f}% of the pixels differ (accepted up to {tolerance * 100:.3f}%)", ratio, buf.getvalue()


A11Y_JS = r"""
const issues = []; const seen = new Set();
const sel = (el) => { if (el.id) return '#' + CSS.escape(el.id); let s = el.tagName.toLowerCase();
  const c = (el.getAttribute('class') || '').trim().split(/\s+/)[0]; if (c) s += '.' + CSS.escape(c); return s; };
const add = (rule, impact, el, help) => { const k = rule + '|' + (el ? sel(el) + (el.outerHTML || '').slice(0, 60) : ''); if (seen.has(k)) return; seen.add(k);
  issues.push({rule, impact, target: el ? sel(el) : 'page', html: el ? el.outerHTML.slice(0, 120) : '', help}); };
const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.closest('[aria-hidden="true"]'); };
const text = (el) => (el.innerText || el.textContent || '').trim();
const labelled = (el) => { if (el.getAttribute('aria-label') && el.getAttribute('aria-label').trim()) return true;
  const lb = el.getAttribute('aria-labelledby'); if (lb && lb.split(/\s+/).some((i) => { const t = document.getElementById(i); return t && text(t); })) return true;
  if (el.title && el.title.trim()) return true; return false; };
const walk = (root, fn) => { for (const el of root.querySelectorAll('*')) { fn(el); if (el.shadowRoot) walk(el.shadowRoot, fn); } };
if (!document.documentElement.getAttribute('lang')) add('html-lang', 'serious', document.documentElement, 'The page has no lang attribute: screen readers cannot pick a voice.');
if (!document.title.trim()) add('document-title', 'serious', null, 'The page has no title.');
const ids = new Map();
walk(document, (el) => {
  const tag = el.tagName.toLowerCase();
  if (el.id) { ids.set(el.id, (ids.get(el.id) || 0) + 1); }
  if (!visible(el) && tag !== 'html') return;
  if (tag === 'img' && !el.hasAttribute('alt') && el.getAttribute('role') !== 'presentation' && !labelled(el)) add('image-alt', 'critical', el, 'An image has no alt text (use alt="" if it is decorative).');
  if ((tag === 'button' || el.getAttribute('role') === 'button') && !text(el) && !labelled(el) && !el.querySelector('img[alt]:not([alt=""]), svg title')) add('button-name', 'critical', el, 'A button has no accessible name.');
  if (tag === 'a' && el.hasAttribute('href') && !text(el) && !labelled(el) && !el.querySelector('img[alt]:not([alt=""]), svg title')) add('link-name', 'serious', el, 'A link has no text a screen reader can read.');
  if ((tag === 'input' && !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.type || '').toLowerCase())) || tag === 'select' || tag === 'textarea') {
    const hasLabel = (el.labels && el.labels.length && [...el.labels].some((l) => text(l))) || labelled(el) || el.placeholder;
    if (!hasLabel) add('label', 'critical', el, 'A form field has no label.'); }
  if (/^h[1-6]$/.test(tag) && !text(el) && !labelled(el)) add('empty-heading', 'minor', el, 'A heading is empty.');
  if (el.hasAttribute('tabindex') && parseInt(el.getAttribute('tabindex'), 10) > 0) add('tabindex', 'serious', el, 'A positive tabindex changes the natural tab order.');
  if (el.getAttribute('aria-hidden') === 'true' && el.matches('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')) add('aria-hidden-focus', 'serious', el, 'A focusable element is hidden from assistive technology.');
});
for (const [id, n] of ids) if (n > 1) add('duplicate-id', 'minor', document.getElementById(id), 'The id "' + id + '" is used ' + n + ' times.');
return issues;
"""
IMPACTS = ("minor", "moderate", "serious", "critical")


def a11y_problems(issues, level="serious"):
    """Issues at `level` or worse, as one readable message (None when the page is clean at that level)."""
    level = (level or "serious").strip().lower()
    if level not in IMPACTS:
        level = "serious"
    bad = [i for i in issues if IMPACTS.index(i.get("impact", "minor")) >= IMPACTS.index(level)]
    if not bad:
        return None
    lines = [f"{i['impact']}: {i['help']} [{i['rule']}: {i['target']}]" for i in bad[:6]]
    return f"{len(bad)} accessibility issue(s) at '{level}' or worse. " + " | ".join(lines) + (f" | …and {len(bad) - 6} more" if len(bad) > 6 else "")


MOCK_JS = r"""
(() => {
  const rules = %RULES%;
  window.__aiautoMocks = rules;
  if (window.__aiautoMockInstalled) return;
  window.__aiautoMockInstalled = true;
  const find = (url, method) => {
    for (const r of window.__aiautoMocks || []) {
      const m = (r.method || '*').toUpperCase();
      if (m !== '*' && m !== String(method || 'GET').toUpperCase()) continue;
      let hit = false; const p = r.match;
      if (p.startsWith('re:')) { try { hit = new RegExp(p.slice(3)).test(url); } catch (e) {} }
      else if (p.includes('*')) hit = new RegExp('^' + p.split('*').map((x) => x.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$').test(url);
      else hit = url.includes(p);
      if (hit) return r;
    }
    return null;
  };
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = new URL(typeof input === 'string' ? input : (input.url || String(input)), location.href).href;
    const r = find(url, (init && init.method) || (typeof input !== 'string' && input.method) || 'GET');
    if (!r) return origFetch.apply(this, arguments);
    const go = () => r.block ? Promise.reject(new TypeError('Failed to fetch (blocked by the test)'))
      : new Response(r.body == null ? '' : r.body, {status: r.status || 200, headers: {'Content-Type': r.contentType || 'application/json'}});
    return r.delay ? new Promise((res, rej) => setTimeout(() => { try { res(go()); } catch (e) { rej(e); } }, r.delay)) : Promise.resolve(go()).then((x) => x);
  };
  const Open = XMLHttpRequest.prototype.open, Send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) { this.__m = {method, url: new URL(url, location.href).href}; return Open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    const r = this.__m && find(this.__m.url, this.__m.method);
    if (!r) return Send.apply(this, arguments);
    const x = this;
    const done = () => {
      if (r.block) { x.dispatchEvent(new Event('error')); if (x.onerror) x.onerror(new Event('error')); return; }
      const def = (k, v) => Object.defineProperty(x, k, {value: v, configurable: true});
      def('readyState', 4); def('status', r.status || 200); def('statusText', 'OK'); def('responseText', r.body == null ? '' : r.body); def('response', r.body == null ? '' : r.body);
      x.getResponseHeader = (h) => (h.toLowerCase() === 'content-type' ? (r.contentType || 'application/json') : null);
      x.getAllResponseHeaders = () => 'content-type: ' + (r.contentType || 'application/json') + '\r\n';
      x.dispatchEvent(new Event('readystatechange')); if (x.onreadystatechange) x.onreadystatechange();
      x.dispatchEvent(new Event('load')); if (x.onload) x.onload(); x.dispatchEvent(new Event('loadend'));
    };
    setTimeout(done, r.delay || 0);
  };
})();
"""


def mock_js(rules):
    return MOCK_JS.replace("%RULES%", json.dumps(rules))


def parse_json_spec(text):
    """A step's value that carries a small JSON document; anything else is treated as plain text by the caller."""
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            return json.loads(t)
        except ValueError:
            return None
    return None


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
            remote = os.getenv("REMOTE_URL")      # Playwright browser server (ws://) or DevTools endpoint (http://)
            if remote:
                self._browser = bt.connect(remote) if remote.startswith(("ws://", "wss://")) else self._pw.chromium.connect_over_cdp(remote)
            else:
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
        from playwright.sync_api import expect
        expect.set_options(timeout=self.timeout_ms)         # assertions wait as long as steps do
        self.ctx.on("page", lambda p: setattr(self, "_page", p))

    def _remote(self, o):
        """Selenium Grid / BrowserStack / Sauce Labs / LambdaTest: REMOTE_URL + REMOTE_CAPS (JSON capabilities)."""
        from selenium import webdriver
        for k, v in json.loads(os.getenv("REMOTE_CAPS") or "{}").items():
            o.set_capability(k, v)
        return webdriver.Remote(command_executor=os.environ["REMOTE_URL"], options=o)

    def _start_selenium(self):
        from selenium import webdriver
        b = self.browser_name
        if os.getenv("REMOTE_URL") and b in ("safari", "webkit"):
            kind, binary, driver = "safari", None, None
        else:
            kind, binary, driver = selenium_browser(b)
        service = None if os.getenv("REMOTE_URL") else selenium_service(kind, driver, binary)
        if kind == "safari":
            self.driver = self._remote(webdriver.SafariOptions()) if os.getenv("REMOTE_URL") else webdriver.Safari()
        elif kind == "firefox":
            o = webdriver.FirefoxOptions()
            if self.headless:
                o.add_argument("-headless")
            if binary:
                o.binary_location = binary
            o.set_capability("unhandledPromptBehavior", "ignore")
            if os.getenv("REMOTE_URL"):
                self.driver = self._remote(o)
            else:
                self.driver = webdriver.Firefox(options=o, service=service) if service else webdriver.Firefox(options=o)
        else:
            edge = kind == "edge"
            o = webdriver.EdgeOptions() if edge else webdriver.ChromeOptions()
            o.add_argument(f"--window-size={self.viewport[0]},{self.viewport[1]}")
            # no browser UI over the page (Playwright's Chromium has these off too): HTTPS-First interstitial on
            # http:// pages, and the password leak warning that swallows clicks after signing in with a known password
            o.add_argument("--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable,HttpsFirstModeV2ForEngagedSites,"
                           "PasswordLeakDetection,PasswordCheck")
            prefs = {"https_only_mode_enabled": False, "https_upgrades_enabled": False, "profile.password_manager_leak_detection": False}
            if not os.getenv("CHROME_PROFILE_DIR"):
                prefs.update({"credentials_enable_service": False, "profile.password_manager_enabled": False})
            o.add_experimental_option("prefs", prefs)
            o.set_capability("unhandledPromptBehavior", "ignore")
            if self.headless:
                o.add_argument("--headless=new")
                o.add_argument("--no-sandbox")
            if binary:
                o.binary_location = binary
            if os.getenv("CHROME_PROFILE_DIR"):
                o.add_argument("--user-data-dir=" + os.environ["CHROME_PROFILE_DIR"])
                if os.getenv("CHROME_PROFILE_NAME"):
                    o.add_argument("--profile-directory=" + os.environ["CHROME_PROFILE_NAME"])
            cls = webdriver.Edge if edge else webdriver.Chrome
            if os.getenv("REMOTE_URL"):
                self.driver = self._remote(o)
            else:
                self.driver = cls(options=o, service=service) if service else cls(options=o)
            try:
                self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": OPEN_SHADOW_JS})
                if os.getenv("STORAGE_STATE"):
                    state = json.loads(Path(os.environ["STORAGE_STATE"]).read_text(encoding="utf-8"))
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

    # ------------------------------------------------------------ page text (every frame + open shadow roots)
    def page_text(self) -> str:
        parts = []
        for f in self._page.frames:            # frameset frames, nested and cross-origin iframes
            try:
                parts.append(f.evaluate(FRAME_TEXT_JS))
            except Exception:
                pass
        return "\n".join(parts)

    def expect_page_text(self, text: str, present: bool = True, settle_s: float = 2.0) -> None:
        """Like the recording engine: the text anywhere on the page (frames and shadow roots included), within the
        step timeout; absent = it does not show up while the page settles."""
        want = " ".join(str(text).split()).lower()
        end = time.time() + (self.timeout_ms / 1000 if present else settle_s)
        while True:
            got = " ".join(self.page_text().split()).lower()
            if present and want in got:
                return
            if not present and want in got:
                raise AssertionError(f"expected the page not to contain {text!r}")
            if time.time() > end:
                if present:
                    raise AssertionError(f"expected the page to contain {text!r}; it shows {got[:300]!r}")
                return
            if present:
                rehover()       # a held hover was taken away: hover again
            time.sleep(0.25)

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
        lst.write_text("\n".join(lines), encoding="utf-8")
        out = self.dir / "video.webm"
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=12,format=yuv420p"   # Safari screenshots carry alpha (RGBA PNG)
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
                if getattr(self, "ctx", None) is None:          # the browser never started: nothing to close
                    if getattr(self, "_pw", None):
                        self._pw.stop()
                    return None
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
                if getattr(self, "driver", None) is not None:
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
                text = re.sub(rf"(?<![\w.\-/:@]){re.escape(s)}(?![\w.\-/:@])", MASK, text)
        return text

    def skip(self, n, title, reason):
        self.results.append({"n": n, "title": self._m(title), "status": "skipped", "error": reason, "ms": 0})

    def hover(self, do):
        """Hover now and hold it: hover again before the checks and waits that follow, until the next action."""
        _HELD[0] = do
        do()

    # ------------------------------------------------------------ extras: visual, accessibility, API, network mocks
    def _eval(self, script):
        if self.engine == "playwright":
            return self._page.evaluate("() => {" + script + "}")
        self.driver.switch_to.default_content()
        return self.driver.execute_script(script)

    def visual(self, spec=""):
        """Compare the page with its baseline screenshot (BASELINES_DIR, default ./baselines/<test>/); UPDATE_BASELINES=1 accepts the new look."""
        name, _, tol = (spec or "").partition(":")
        self._visuals = getattr(self, "_visuals", [])
        name = name.strip() or f"step-{len(self._visuals) + 1}"
        self._visuals.append(name)
        try:
            tolerance = float(tol) / 100 if tol.strip() else 0.001
        except ValueError:
            raise AssertionError(f"'{tol}' is not a tolerance (a percentage, e.g. {name}:0.5)")
        png = self._page.screenshot() if self.engine == "playwright" else self.driver.get_screenshot_as_png()
        safe = re.sub(r"[^\w.-]", "_", name)
        base = Path(os.getenv("BASELINES_DIR") or "baselines") / _slug(self.name) / (safe + ".png")
        state, msg, _ratio, diff = visual_compare(png, base, tolerance, _env_bool("UPDATE_BASELINES", False))
        (self.dir / "shots" / f"visual_{safe}.png").write_bytes(png)
        if diff:
            (self.dir / "shots" / f"visual_{safe}_diff.png").write_bytes(diff)
        if state == "different":
            raise AssertionError(f"'{name}' does not match its baseline: {msg} (UPDATE_BASELINES=1 accepts the new look)")

    def a11y(self, level=""):
        problem = a11y_problems(self._eval(A11Y_JS), level)
        if problem:
            raise AssertionError(problem)

    def api(self, target, spec=""):
        """Call an API and check the answer. Returns the values the step saves for later steps."""
        method, url = split_api_target(target)
        doc = parse_json_spec(spec) or {}
        if not url.startswith(("http://", "https://")):
            from urllib.parse import urljoin
            current = self._page.url if self.engine == "playwright" else self.driver.current_url
            url = urljoin(current, url)
        status, _h, text = api_request(method, url, doc.get("headers"), doc.get("body"))
        problem, saved = check_api(status, text, doc)
        if problem:
            raise AssertionError(f"{method} {url.split('?')[0]}: {problem}")
        return saved

    def mock(self, pattern, spec=""):
        """Answer or block the page's fetch / XHR calls to URLs matching `pattern` (Chrome and Edge keep it across navigations)."""
        doc = parse_json_spec(spec) or {}
        self._mocks = getattr(self, "_mocks", [])
        if doc.get("clear"):
            self._mocks.clear()
        else:
            body = doc.get("body")
            rule = {"match": pattern, **{k: doc[k] for k in ("status", "method", "delay", "contentType", "block") if k in doc},
                    "body": body if isinstance(body, str) or body is None else json.dumps(body)}
            self._mocks[:] = [m for m in self._mocks if (m["match"], m.get("method")) != (rule["match"], rule.get("method"))] + [rule]
        js = mock_js(self._mocks)
        if self.engine == "playwright":
            self.ctx.add_init_script(js)
            for pg in self.ctx.pages:
                try:
                    pg.evaluate(js)
                except Exception:
                    pass
        else:
            try:
                self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
            except Exception:
                pass
            self.driver.switch_to.default_content()
            self.driver.execute_script(js)

    @contextmanager
    def step(self, n, title, action="", target=None, value=None, locator="", optional=False):
        """Wraps one step: located box -> action -> done box -> screenshot; failure -> red box + screenshot."""
        verb = action.replace("_", " ") or "step"
        if action in HOVER_PASSIVE:   # keep a hover held for the checks after it (another window or the real
            rehover()                  # mouse can take it away)
        elif action != "hover":
            _HELD[0] = None
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
            shot = self.dir / "shots" / f"step_{n:02d}.png"
            for attempt in range(4):        # a step that navigates can leave the page mid-load: wait and retry
                try:
                    if self.engine == "playwright":
                        self._page.screenshot(path=str(shot))
                    else:
                        self.driver.switch_to.default_content()
                        self.driver.save_screenshot(str(shot))
                    rec["shot"] = f"shots/{shot.name}"
                    break
                except Exception:
                    time.sleep(0.4)
                    if self.engine == "playwright":
                        try:
                            self._page.wait_for_load_state("domcontentloaded", timeout=5000)
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
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
            f'failures="{fails}" skipped="{skips}" time="{s["duration_ms"] / 1000:.3f}">\n' + "\n".join(cases) + "\n</testsuite>\n", encoding="utf-8")

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
