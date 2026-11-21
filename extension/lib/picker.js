// "Pick the element": while it is on, the page under the pointer is outlined and the next click chooses that element (the click itself does
// nothing to the page). Runs in every frame; the element is described with the engines' own fingerprint, like a recording does.
(() => {
  const S = () => window.__aiautoShared;
  const isTop = window === window.top;
  if (window.__aiautoPicker) { window.__aiautoPicker.start(); return; }
  let on = false, style = null, label = null;
  const send = (m) => { try { chrome.runtime.sendMessage(m); } catch (e) { /* the extension was reloaded */ } };
  const first = (ev) => { let e = ev.composedPath ? ev.composedPath()[0] : ev.target; if (e && e.nodeType !== 1) e = e && e.parentElement; return e; };
  const ACTIONABLE = "a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox],[role=textbox],[onclick],[tabindex],[draggable=true]";
  function pick(e) {
    for (let n = e, k = 0; n && n.nodeType === 1 && k < 6; n = n.parentElement || (n.getRootNode && n.getRootNode().host), k++) if (n.matches && n.matches(ACTIONABLE)) return n;
    return e;
  }
  const hover = (ev) => { const e = first(ev); if (e) try { S().box(pick(e), { state: "located", label: "Click to choose", ttl: 60000 }); } catch (x) { /* no box */ } };
  const stopEvent = (ev) => { ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation(); };
  const click = (ev) => {
    stopEvent(ev);
    const el = pick(first(ev)); let d = null;
    try { d = S().describe(el, null); delete d.anc; } catch (e) { /* not describable */ }
    api.stop();
    send({ type: "pick-result", el: d, frame: !isTop });
  };
  const key = (ev) => { if (ev.key === "Escape") { stopEvent(ev); api.stop(); send({ type: "pick-cancel" }); } };
  const swallow = (ev) => { stopEvent(ev); };
  const api = window.__aiautoPicker = {
    start() {
      if (on) return; on = true;
      style = document.createElement("style"); style.setAttribute("data-aiauto-pick", "1");
      style.textContent = "html, html * { cursor: crosshair !important; }";
      (document.head || document.documentElement).appendChild(style);
      addEventListener("mousemove", hover, true); addEventListener("click", click, true); addEventListener("keydown", key, true);
      for (const t of ["mousedown", "mouseup", "pointerdown", "pointerup", "dblclick", "contextmenu"]) addEventListener(t, swallow, true);
    },
    stop() {
      if (!on) return; on = false;
      removeEventListener("mousemove", hover, true); removeEventListener("click", click, true); removeEventListener("keydown", key, true);
      for (const t of ["mousedown", "mouseup", "pointerdown", "pointerup", "dblclick", "contextmenu"]) removeEventListener(t, swallow, true);
      if (style) style.remove(); style = null;
      try { S().clearBox(document.documentElement); } catch (e) { /* no box */ }
    },
  };
  api.start();
})();
