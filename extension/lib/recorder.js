// Runs in every frame of a tab being recorded (the extension's own, isolated world: the page can't see or change it). Listens to
// what the person really does, describes the element each time with the engines' own fingerprint (role, name, label, text and
// a list of locators), and hands the events to the service worker. The hub turns them into steps.
(() => {
  if (window.__aiautoRecorder) return;
  window.__aiautoRecorder = true;
  const S = () => window.__aiautoShared;
  const norm = (s) => String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  const send = (ev) => { try { chrome.runtime.sendMessage({ type: "rec-event", ev, frame: window !== window.top }); } catch (e) { /* the extension was reloaded */ } };

  const TEXTY = (e) => e.tagName === "TEXTAREA" || (e.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset", "file", "image", "range", "color"].includes((e.type || "").toLowerCase()));
  const ACTIONABLE = "a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[onclick],[tabindex]";

  function elementOf(ev) {
    let e = ev.composedPath ? ev.composedPath()[0] : ev.target;
    if (e && e.nodeType !== 1) e = e && e.parentElement;
    if (!e) return null;
    let best = e;
    for (let n = e, k = 0; n && n.nodeType === 1 && k < 6; n = n.parentElement, k++) {
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
  let pending = null;                              // typed text that no `change` has reported yet
  function flushPending() {
    if (!pending) return;
    const { el, secret } = pending; pending = null;
    const d = describe(el); if (!d) return;
    send({ type: "input", el: d, value: el.isContentEditable ? el.innerText : el.value, secret });
  }

  addEventListener("click", (ev) => {
    if (!ev.isTrusted) return;
    const el = elementOf(ev); if (!el) return;
    if (TEXTY(el) || el.tagName === "SELECT" || el.tagName === "OPTION") return;      // typing / choosing are recorded by their own events
    if (el.tagName === "INPUT" && ["checkbox", "radio"].includes((el.type || "").toLowerCase())) return;   // the change event says what it ended as
    flushPending();
    const d = describe(el); if (!d) return;
    flash(el, "● recorded"); send({ type: "click", el: d, url: location.href });
  }, opts);
  addEventListener("dblclick", (ev) => { if (!ev.isTrusted) return; const el = elementOf(ev), d = describe(el); if (d) send({ type: "dblclick", el: d }); }, opts);
  addEventListener("contextmenu", (ev) => { if (!ev.isTrusted) return; const el = elementOf(ev), d = describe(el); if (d) send({ type: "rightclick", el: d }); }, opts);

  addEventListener("input", (ev) => {
    if (!ev.isTrusted) return;
    const el = ev.target && ev.target.nodeType === 1 ? (ev.composedPath ? ev.composedPath()[0] : ev.target) : null;
    if (el && (TEXTY(el) || el.isContentEditable)) pending = { el, secret: (el.type || "").toLowerCase() === "password" };
  }, opts);

  addEventListener("change", (ev) => {
    if (!ev.isTrusted) return;
    const el = ev.composedPath ? ev.composedPath()[0] : ev.target; if (!el || el.nodeType !== 1) return;
    const d = describe(el); if (!d) return;
    const type = (el.type || "").toLowerCase();
    if (el.tagName === "SELECT") { pending = null; send({ type: "select", el: d, value: Array.from(el.selectedOptions).map((o) => norm(o.text)).join("|") }); flash(el, "● recorded"); }
    else if (type === "checkbox" || type === "radio") { if (type === "radio" && !el.checked) return; send({ type: "check", el: d, checked: !!el.checked }); flash(el, "● recorded"); }
    else if (type === "file") send({ type: "upload", el: d });
    else if (TEXTY(el)) { pending = null; send({ type: "input", el: d, value: el.value, secret: type === "password" }); flash(el, "● recorded"); }
  }, opts);

  addEventListener("keydown", (ev) => {
    if (!ev.isTrusted || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (!["Enter", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(ev.key)) return;
    const el = elementOf(ev); if (!el) return;
    if (el.tagName === "SELECT" || (ev.key.startsWith("Arrow") && !TEXTY(el))) return;                     // choosing in a list is the select / check event
    if (ev.key === "Enter" && (["BUTTON", "A", "SUMMARY", "SELECT"].includes(el.tagName) || ["checkbox", "radio", "button", "submit"].includes((el.type || "").toLowerCase()))) return;   // that already clicks it
    if (ev.key === "Enter" && el.tagName === "TEXTAREA") return;                                             // a new line, not a submit
    if (ev.key === "Enter") flushPending();
    const d = describe(el); send({ type: "key", key: ev.key, el: d });
  }, opts);

  addEventListener("pagehide", flushPending, opts);
  addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushPending(); }, opts);
})();
