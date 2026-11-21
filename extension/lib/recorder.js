// Runs in every frame of a tab being recorded (the extension's own, isolated world: the page can't see or change it). Watches what the
// person really does, describes the element each time with the engines' own fingerprint (role, name, label, text and a list of
// locators), and hands the events to the service worker. The hub turns them into steps and scripts.
//
// Captured: clicks (single, double, right), typing (text, number, date, contenteditable editors, ...), range and colour inputs, dropdown
// choices (single and multiple), checkboxes and radios, file uploads, keys and shortcuts, hovers that reveal something, drag and drop
// (HTML5 and mouse), scrolling, alert / confirm / prompt (what was shown, what was answered), and new tabs / navigation (by the worker).
(() => {
  if (window.__aiautoRecorder) return;
  window.__aiautoRecorder = true;
  const S = () => window.__aiautoShared;
  const norm = (s) => String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  // Events go out in the order the person did things: typing that is still waiting for its `change` is sent before anything that came after it.
  const rawSend = (ev) => { try { chrome.runtime.sendMessage({ type: "rec-event", ev, frame: window !== window.top }); } catch (e) { /* the extension was reloaded */ } };
  const send = (ev) => { if (pending && ev.type !== "input") flushPending(); rawSend(ev); };
  const isTop = window === window.top;
  let pending = null;                                   // typed text that no `change` has reported yet

  const NONTEXT = ["checkbox", "radio", "button", "submit", "reset", "file", "image", "range", "color", "hidden"];
  const TEXTY = (e) => e && e.nodeType === 1 && (e.tagName === "TEXTAREA" || (e.tagName === "INPUT" && !NONTEXT.includes((e.type || "").toLowerCase())));
  const EDITABLE = (e) => e && e.nodeType === 1 && (e.isContentEditable === true) && !TEXTY(e);
  const VALUE_INPUT = (e) => e && e.tagName === "INPUT" && ["range", "color"].includes((e.type || "").toLowerCase());
  const ACTIONABLE = "a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox],[role=textbox],[onclick],[tabindex],[draggable=true]";

  const first = (ev) => (ev.composedPath ? ev.composedPath()[0] : ev.target);
  function elementOf(ev) {
    let e = first(ev);
    if (e && e.nodeType !== 1) e = e && e.parentElement;
    return e ? actionableFrom(e) : null;
  }
  function actionableFrom(e) {
    let best = e;
    for (let n = e, k = 0; n && n.nodeType === 1 && k < 6; n = n.parentElement || (n.getRootNode && n.getRootNode().host), k++) {
      if (n.matches && n.matches(ACTIONABLE)) { best = n; break; }
      try { if (getComputedStyle(n).cursor === "pointer") { best = n; break; } } catch (err) { /* detached */ }
    }
    if (best.tagName === "LABEL" && best.control) best = best.control;
    return best;
  }
  function describe(el) {
    if (!el || !S() || !S().describe) return null;
    try { const d = S().describe(el, null); delete d.anc; return d; } catch (e) { return null; }
  }
  const flash = (el, text) => { try { S().box(el, { state: "done", label: text, ttl: 700 }); } catch (e) { /* no box */ } };
  const opts = { capture: true, passive: true };

  // ------------------------------------------------------------------------------------------------ typing
  function flushPending() {
    if (!pending) return;
    const { el, secret } = pending; pending = null;
    const d = describe(el); if (!d) return;
    rawSend({ type: "input", el: d, value: el.isContentEditable && !TEXTY(el) ? el.innerText : el.value, secret });
  }
  addEventListener("input", (ev) => {
    if (!ev.isTrusted) return;
    const el = first(ev); if (!el || el.nodeType !== 1) return;
    if (TEXTY(el) || EDITABLE(el)) pending = { el, secret: (el.type || "").toLowerCase() === "password" };
  }, opts);
  addEventListener("focusout", (ev) => { if (!ev.isTrusted) return; const el = first(ev); if (pending && el === pending.el) flushPending(); }, opts);
  addEventListener("pagehide", flushPending, opts);
  addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushPending(); }, opts);

  addEventListener("change", (ev) => {
    if (!ev.isTrusted) return;
    const el = first(ev); if (!el || el.nodeType !== 1) return;
    const d = describe(el); if (!d) return;
    const type = (el.type || "").toLowerCase();
    if (el.tagName === "SELECT") { pending = null; send({ type: "select", el: d, value: Array.from(el.selectedOptions).map((o) => norm(o.text)).join("|") }); flash(el, "● recorded"); }
    else if (type === "checkbox" || type === "radio") { if (type === "radio" && !el.checked) return; send({ type: "check", el: d, checked: !!el.checked }); flash(el, "● recorded"); }
    else if (type === "file") send({ type: "upload", el: d, files: Array.from(el.files || []).map((f) => f.name) });
    else if (VALUE_INPUT(el)) { send({ type: "input", el: d, value: el.value }); flash(el, "● recorded"); }
    else if (TEXTY(el)) { pending = null; send({ type: "input", el: d, value: el.value, secret: type === "password" }); flash(el, "● recorded"); }
  }, opts);

  // ------------------------------------------------------------------------------------------------ clicks
  let dragged = false;                                  // a drag ends with a click event the person did not mean
  addEventListener("click", (ev) => {
    if (!ev.isTrusted) return;
    if (dragged) { dragged = false; return; }
    const el = elementOf(ev); if (!el) return;
    if (TEXTY(el) || EDITABLE(el) || el.tagName === "SELECT" || el.tagName === "OPTION" || VALUE_INPUT(el)) return;      // their own events record typing / choosing
    const t = (el.type || "").toLowerCase();
    if (el.tagName === "INPUT" && (t === "checkbox" || t === "radio" || t === "file")) return;
    flushPending();
    const d = describe(el); if (!d) return;
    flash(el, "● recorded"); send({ type: "click", el: d, url: location.href });
  }, opts);
  addEventListener("dblclick", (ev) => { if (!ev.isTrusted) return; const el = elementOf(ev), d = describe(el); if (d) send({ type: "dblclick", el: d }); }, opts);
  addEventListener("contextmenu", (ev) => { if (!ev.isTrusted) return; const el = elementOf(ev), d = describe(el); if (d) send({ type: "rightclick", el: d }); }, opts);

  // ------------------------------------------------------------------------------------------------ keys
  const MODS = new Set(["Control", "Shift", "Alt", "Meta", "AltGraph", "CapsLock", "NumLock", "ScrollLock", "Dead", "Process", "Unidentified"]);
  const NAMED = new Set(["Enter", "Escape", "Tab", "Home", "End", "PageUp", "PageDown", "Delete", "Backspace", "Insert", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"]);
  addEventListener("keydown", (ev) => {
    if (!ev.isTrusted || MODS.has(ev.key)) return;
    const el = elementOf(ev); if (!el) return;
    const typing = TEXTY(el) || EDITABLE(el);
    const combo = ev.ctrlKey || ev.metaKey || ev.altKey;
    let key = ev.key === " " ? "Space" : ev.key;
    if (!NAMED.has(key) && !(key.length === 1 && (combo || !typing))) return;                    // plain letters typed in a field are the field's value
    if (combo && typing && /^[acvxzy]$/i.test(key)) return;                                       // clipboard / undo inside a field: its value says it all
    if (el.tagName === "SELECT" && !combo) return;                                                 // choosing in a list: the select event
    if (VALUE_INPUT(el) && key.startsWith("Arrow")) return;                                         // sliders: the change event
    if (typing && (key === "Backspace" || key === "Delete") && !combo) return;                      // editing the text: the field's value
    if (key === "Enter" && !combo && (["BUTTON", "A", "SUMMARY", "SELECT"].includes(el.tagName) || ["checkbox", "radio", "button", "submit", "file"].includes((el.type || "").toLowerCase()))) return;   // that already clicks it
    if (key === "Enter" && el.tagName === "TEXTAREA") return;                                       // a new line, not a submit
    if (key === "Enter" || key === "Tab") flushPending();
    const parts = [];
    if (ev.ctrlKey) parts.push("Control"); if (ev.metaKey) parts.push("Meta"); if (ev.altKey) parts.push("Alt"); if (ev.shiftKey && (key.length > 1 || combo)) parts.push("Shift");
    parts.push(key.length === 1 ? key.toLowerCase() : key);
    send({ type: "key", key: parts.join("+"), el: describe(el) });
  }, opts);

  // ------------------------------------------------------------------------------------------------ hover that revealed something
  const INTERACTIVE = "a,button,input,select,textarea,summary,[role=button],[role=link],[role=menuitem],[role=option],[role=tab],[tabindex]";
  const visibleSet = () => {
    const out = new Set();
    let n = 0;
    for (const e of document.querySelectorAll(INTERACTIVE)) { if (++n > 600) break; const r = e.getBoundingClientRect(); if (r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden") out.add(e); }
    return out;
  };
  // A hover "mattered" if something interactive is visible now that was not while the pointer was over the previous element. The state
  // under the PREVIOUS element is kept as the baseline: by the time `mouseover` reaches us the browser has already applied :hover, so a
  // menu opened by this very hover would otherwise already be in a snapshot taken here (the order differs between operating systems).
  let hoverTimer = null, hoverEl = null, baseline = null, lastHovered = null;
  addEventListener("mouseover", (ev) => {
    if (!ev.isTrusted) return;
    const el = elementOf(ev); if (!el || el === hoverEl) return;
    const before = baseline || visibleSet();
    baseline = visibleSet();
    hoverEl = el;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      if (!hoverEl || hoverEl === lastHovered) return;
      const now = visibleSet(); let revealed = false;
      for (const e of now) if (!before.has(e)) { revealed = true; break; }                  // something that was not there is now: the hover mattered
      if (!revealed) return;
      const d = describe(hoverEl); if (!d) return;
      lastHovered = hoverEl; flash(hoverEl, "● hover"); send({ type: "hover", el: d });
    }, 450);
  }, opts);
  // the state of the page before the pointer has touched anything: the baseline for the very first hover
  const initBaseline = () => { if (!baseline) baseline = visibleSet(); };
  if (document.readyState !== "loading") initBaseline(); else addEventListener("DOMContentLoaded", initBaseline, opts);
  addEventListener("load", initBaseline, opts);
  addEventListener("mouseout", (ev) => { if (!ev.relatedTarget) { clearTimeout(hoverTimer); hoverEl = null; } }, opts);

  // ------------------------------------------------------------------------------------------------ drag and drop
  let dragSrc = null, down = null;
  addEventListener("dragstart", (ev) => { if (ev.isTrusted) dragSrc = actionableFrom(first(ev)); }, opts);
  addEventListener("drop", (ev) => {
    if (!ev.isTrusted || !dragSrc) return;
    const dst = elementOf(ev), a = describe(dragSrc), b = describe(dst);
    if (a && b) { dragged = true; flash(dst, "● drop"); send({ type: "drag", el: a, target: b }); }
    dragSrc = null;
  }, opts);
  addEventListener("mousedown", (ev) => { if (ev.isTrusted && ev.button === 0) down = { x: ev.clientX, y: ev.clientY, el: elementOf(ev), sel: !!String(getSelection() || "") }; }, opts);
  addEventListener("mouseup", (ev) => {
    const d0 = down; down = null;
    if (!ev.isTrusted || !d0 || dragSrc || ev.button !== 0 || !d0.el) return;
    if (Math.hypot(ev.clientX - d0.x, ev.clientY - d0.y) < 12) return;
    const src = d0.el, dst = elementOf(ev);
    if (!dst || dst === src || TEXTY(src) || EDITABLE(src) || VALUE_INPUT(src) || src.tagName === "SELECT" || String(getSelection() || "")) return;     // selecting text or dragging a slider is not a drag
    if (src.contains(dst) || dst.contains(src)) return;
    const a = describe(src), b = describe(dst);
    if (a && b) { dragged = true; send({ type: "drag", el: a, target: b }); }
  }, opts);

  // ------------------------------------------------------------------------------------------------ scrolling (the page; settled)
  let scrollTimer = null, lastY = 0;
  if (isTop) addEventListener("scroll", (ev) => {
    if (!ev.isTrusted) return;
    const t = ev.target;
    if (t !== document && t !== document.documentElement && t !== document.body) return;
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      const d = document.scrollingElement || document.documentElement, y = Math.round(d.scrollTop), max = Math.max(0, d.scrollHeight - d.clientHeight);
      if (Math.abs(y - lastY) < 300) return;
      send({ type: "scroll", y, from: lastY, max }); lastY = y;
    }, 700);
  }, opts);

  // ------------------------------------------------------------------------------------------------ alert / confirm / prompt (from lib/dialogs.js in the page's world)
  addEventListener("message", (ev) => {
    const m = ev.data;
    if (ev.source !== window || !m || m.__aiauto !== "dialog") return;
    send({ type: "dialog", kind: m.kind, message: m.message, accepted: m.kind === "prompt" ? m.result !== null : !!m.result, answer: m.kind === "prompt" ? m.result : undefined });
  });
})();
