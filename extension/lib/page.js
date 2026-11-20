// Runs INSIDE the page (main world, every frame). Looks elements up by the uid the DOM walk (shared.js) gave them and answers
// questions about them; the clicking and typing themselves are done by the service worker with real browser input.
(() => {
  if (window.__aiautoPage) return;
  const S = () => window.__aiautoShared;
  const el = (uid) => (window.__aiautoEls || [])[uid - 1];
  const norm = (s) => String(s == null ? "" : s).replace(/\s+/g, " ").trim();

  // The element's centre in the TOP window's viewport, walking out through same-origin iframes. `local` is true when this
  // frame is cross-origin (its own parent can't be asked): the point is then relative to this frame, and the caller
  // falls back to acting from inside the frame.
  function centre(e) {
    const r = e.getBoundingClientRect();
    let x = r.left + r.width / 2, y = r.top + r.height / 2, win = e.ownerDocument.defaultView, local = false;
    try {
      while (win && win !== win.top) {
        const f = win.frameElement;
        if (!f) { local = true; break; }
        const fr = f.getBoundingClientRect(), cs = win.parent.getComputedStyle(f);
        x += fr.left + parseFloat(cs.borderLeftWidth || 0) + parseFloat(cs.paddingLeft || 0);
        y += fr.top + parseFloat(cs.borderTopWidth || 0) + parseFloat(cs.paddingTop || 0);
        win = win.parent;
      }
    } catch (err) { local = true; }
    return { x, y, local, w: r.width, h: r.height, lx: r.left + r.width / 2, ly: r.top + r.height / 2 };
  }

  // What is really on top at the element's centre (through open shadow roots)?
  function hitAt(e, lx, ly) {
    let h = e.ownerDocument.elementFromPoint(lx, ly);
    for (let i = 0; i < 6 && h && h.shadowRoot; i++) {
      const n = h.shadowRoot.elementFromPoint(lx, ly);
      if (!n || n === h) break;
      h = n;
    }
    return h;
  }

  const P = window.__aiautoPage = {};

  // Find an element by the locators a recording stored (most robust first). A locator wins only if it points at exactly one
  // visible element; none do -> null, and the caller falls back to ranking by the step's description.
  P.find = (locs) => {
    const cands = window.__aiautoSnapshot ? window.__aiautoSnapshot([]) : [];
    const els = window.__aiautoEls || (window.__aiautoEls = []);
    const eq = (a, b) => norm(a).toLowerCase() === norm(b).toLowerCase();
    const field = { testid: "testid", id: "id", label: "label", placeholder: "placeholder", name: "nameattr", text: "text", alt: "alt", title: "title" };
    const uidOf = (e) => { let i = els.indexOf(e); if (i < 0) { els.push(e); i = els.length - 1; } return i + 1; };
    const shape = (e, strategy) => ({ uid: uidOf(e), strategy, tag: e.tagName.toLowerCase(), type: (e.getAttribute("type") || "").toLowerCase(),
      draggable: e.getAttribute("draggable") === "true", options: e.tagName === "SELECT" ? Array.from(e.options).slice(0, 50).map((o) => norm(o.text)) : [] });
    const vis = (e) => { const r = e.getBoundingClientRect(), st = e.ownerDocument.defaultView.getComputedStyle(e); return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none"; };
    for (const l of locs || []) {
      let hits = [];
      try {
        if (l.strategy === "role") hits = cands.filter((c) => (!l.role || c.role === l.role) && eq(c.name, l.value)).map((c) => els[c.uid - 1]);
        else if (field[l.strategy]) hits = cands.filter((c) => eq(c[field[l.strategy]], l.value)).map((c) => els[c.uid - 1]);
        else if (l.strategy === "css") hits = Array.from(document.querySelectorAll(l.value));
        else if (l.strategy === "xpath") { const r = document.evaluate(l.value, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); for (let i = 0; i < r.snapshotLength; i++) hits.push(r.snapshotItem(i)); }
      } catch (err) { continue; }
      hits = hits.filter((e) => e && e.isConnected && vis(e));
      if (hits.length === 1) return shape(hits[0], l.strategy);
    }
    return null;
  };

  P.exists = (uid) => !!el(uid) && el(uid).isConnected;

  // Scroll into view and report where and how it can be acted on.
  P.prep = (uid) => {
    const e = el(uid);
    if (!e || !e.isConnected) return { ok: false, why: "gone" };
    try { e.scrollIntoView({ block: "center", inline: "center", behavior: "instant" }); } catch (err) { e.scrollIntoView(); }
    const c = centre(e), tag = e.tagName.toLowerCase();
    const hit = hitAt(e, c.lx, c.ly);
    const label = hit && hit.closest ? hit.closest("label") : null;
    const free = !hit || hit === e || e.contains(hit) || hit.contains(e) || (label && (label.control === e || label.contains(e)));
    const st = e.ownerDocument.defaultView.getComputedStyle(e);
    return {
      ok: true, x: c.x, y: c.y, local: c.local, w: c.w, h: c.h, tag, type: (e.getAttribute("type") || "").toLowerCase(),
      disabled: !!e.disabled || e.getAttribute("aria-disabled") === "true", editable: !!e.isContentEditable,
      checked: !!e.checked, free, covering: free ? "" : (hit.tagName.toLowerCase() + (hit.id ? "#" + hit.id : "") + " " + norm(hit.innerText || "").slice(0, 40)),
      hidden: st.visibility === "hidden" || st.display === "none" || (c.w === 0 && c.h === 0),
    };
  };

  P.focusAll = (uid) => {                      // focus and select the content, so typing replaces it
    const e = el(uid); if (!e) return false;
    e.focus();
    if (typeof e.select === "function" && !["checkbox", "radio", "file", "button", "submit"].includes((e.type || "").toLowerCase())) {
      try { e.select(); } catch (err) { /* number / email inputs refuse select(); setSelectionRange fails too */ }
    } else if (e.isContentEditable) {
      const r = e.ownerDocument.createRange(); r.selectNodeContents(e);
      const s = e.ownerDocument.defaultView.getSelection(); s.removeAllRanges(); s.addRange(r);
    }
    return true;
  };

  P.focusEnd = (uid) => {                      // focus and put the caret at the end ("append")
    const e = el(uid); if (!e) return false;
    e.focus();
    try { if (e.setSelectionRange) e.setSelectionRange(e.value.length, e.value.length); } catch (err) { /* not text-like */ }
    return true;
  };

  // Set a value the way a framework-controlled input accepts it (date / range / colour inputs, and typing inside a
  // cross-origin frame where real input can't be aimed).
  P.setValue = (uid, value, append) => {
    const e = el(uid); if (!e) return false;
    e.focus();
    if (e.isContentEditable) { e.ownerDocument.execCommand("selectAll"); e.ownerDocument.execCommand("insertText", false, String(value)); return true; }
    const proto = Object.getPrototypeOf(e), d = Object.getOwnPropertyDescriptor(proto, "value");
    const v = (append ? String(e.value || "") : "") + String(value);
    d && d.set ? d.set.call(e, v) : (e.value = v);
    e.dispatchEvent(new Event("input", { bubbles: true }));
    e.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  // <select>: by visible text, then by value; custom dropdowns are handled by the caller (open it, click the option).
  P.selectOption = (uid, wanted) => {
    const e = el(uid); if (!e || e.tagName.toLowerCase() !== "select") return { ok: false, native: false };
    const labels = Array.from(e.options).map((o) => norm(o.text)), want = (Array.isArray(wanted) ? wanted : [wanted]).map(norm);
    let hit = 0;
    Array.from(e.options).forEach((o) => {
      const on = want.some((w) => w.toLowerCase() === norm(o.text).toLowerCase() || w === o.value);
      if (on) hit++;
      if (e.multiple) o.selected = on; else if (on) e.value = o.value;
    });
    if (hit) { e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }
    return { ok: hit > 0, native: true, options: labels.slice(0, 30) };
  };

  P.text = (uid) => {
    const e = el(uid); if (!e) return "";
    return norm(("value" in e && typeof e.value === "string" && e.value) ? e.value : (e.innerText || e.textContent));
  };
  P.isVisible = (uid) => {
    const e = el(uid); if (!e || !e.isConnected) return false;
    const r = e.getBoundingClientRect(), st = e.ownerDocument.defaultView.getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  };
  P.checked = (uid) => { const e = el(uid); return e ? !!e.checked : null; };

  // Fallback for a cross-origin frame (real input can't be aimed into it): the events a click is made of.
  P.clickDom = (uid, count, right) => {
    const e = el(uid); if (!e) return false;
    const c = e.getBoundingClientRect(), o = { bubbles: true, cancelable: true, composed: true, view: e.ownerDocument.defaultView,
      clientX: c.left + c.width / 2, clientY: c.top + c.height / 2, button: right ? 2 : 0, buttons: right ? 2 : 1 };
    e.dispatchEvent(new PointerEvent("pointerdown", o)); e.dispatchEvent(new MouseEvent("mousedown", o)); e.focus && e.focus();
    e.dispatchEvent(new PointerEvent("pointerup", o)); e.dispatchEvent(new MouseEvent("mouseup", o));
    if (right) e.dispatchEvent(new MouseEvent("contextmenu", o));
    else for (let i = 1; i <= (count || 1); i++) { e.click(); if (i === 2) e.dispatchEvent(new MouseEvent("dblclick", o)); }
    return true;
  };
  P.hoverDom = (uid) => {
    const e = el(uid); if (!e) return false;
    const o = { bubbles: true, composed: true, view: e.ownerDocument.defaultView };
    ["pointerover", "mouseover", "pointerenter", "mouseenter", "pointermove", "mousemove"].forEach((t) => e.dispatchEvent(new MouseEvent(t, o)));
    return true;
  };

  P.maskField = (uid) => { const e = el(uid); if (e && S()) S().mask(e); return !!e; };

  P.scroll = (spec, uid) => {
    if (uid) { const e = el(uid); if (e) e.scrollIntoView({ block: "center", behavior: "instant" }); return true; }
    const [dir, px] = String(spec || "down").split(":"), d = document.scrollingElement || document.documentElement;
    if (dir === "top") d.scrollTo(0, 0);
    else if (dir === "bottom") d.scrollTo(0, d.scrollHeight);
    else if (dir === "up") d.scrollBy(0, -(+px || window.innerHeight * 0.8));
    else d.scrollBy(0, +px || window.innerHeight * 0.8);
    return true;
  };

  P.info = () => ({ url: location.href, title: document.title, ready: document.readyState, top: window === window.top });
  P.pageText = () => (S() ? S().pageText() : (document.body ? document.body.innerText : ""));

  // The same green box the app draws: dashed when located, solid when done, red when failed.
  P.box = (uid, state, label) => {
    const e = el(uid); if (!e || !S()) return false;
    try { return S().box(e, { state, label, ttl: 6000 }); } catch (err) { return false; }
  };
  P.clearBox = () => { try { S().clearBox(document.documentElement); } catch (err) { /* no document */ } };
  P.boxAt = (x, y, state, label) => { try { return S().box(null, { state, label, x, y, ttl: 6000 }); } catch (err) { return false; } };
})();
