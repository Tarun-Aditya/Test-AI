// Runs in the PAGE's own world while recording (before the page's scripts): wraps alert / confirm / prompt so the recorder can see
// what was shown and what the person answered. Nothing else about the dialogs changes.
(() => {
  if (window.__aiautoDialogs) return;
  window.__aiautoDialogs = true;
  const tell = (kind, message, result) => { try { window.postMessage({ __aiauto: "dialog", kind, message: String(message == null ? "" : message), result }, "*"); } catch (e) { /* ignore */ } };
  const a = window.alert, c = window.confirm, p = window.prompt;
  window.alert = function (m) { const r = a.apply(this, arguments); tell("alert", m, true); return r; };
  window.confirm = function (m) { const r = c.apply(this, arguments); tell("confirm", m, !!r); return r; };
  window.prompt = function (m) { const r = p.apply(this, arguments); tell("prompt", m, r); return r; };
})();
