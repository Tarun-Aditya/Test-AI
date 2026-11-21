const $ = (id) => document.getElementById(id);
const ask = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: "No answer" })));
const say = (el, text, cls) => { el.textContent = text; el.className = cls || ""; };

(async () => {
  const s = await chrome.storage.local.get({ hub: "http://localhost:8000", project: "default" });
  $("hub").value = s.hub; $("project").value = s.project;
  $("engine").value = (await chrome.storage.local.get({ engine: "server" })).engine;
  $("report").checked = (await chrome.storage.local.get({ report: true })).report;
  $("shadow").checked = !!(await chrome.storage.local.get({ openShadow: false })).openShadow;
})();

$("engine").onchange = async () => { await chrome.storage.local.set({ engine: $("engine").value }); say($("estatus"), "Saved.", "ok"); };
$("report").onchange = async () => { await chrome.storage.local.set({ report: $("report").checked }); say($("rstatus"), $("report").checked ? "On." : "Off: runs leave nothing on the hub.", "ok"); };
$("shadow").onchange = async () => {
  await chrome.storage.local.set({ openShadow: $("shadow").checked });
  const r = await ask({ type: "shadow", on: $("shadow").checked });
  say($("sstatus"), r.ok ? ($("shadow").checked ? "On: reload the page you want to test." : "Off.") : r.error, r.ok ? "ok" : "bad");
};

async function granted(url) {                 // the hub is on a host the extension may not reach yet? (it has <all_urls>, so yes)
  try { new URL(url); return true; } catch (e) { return false; }
}

$("save").onclick = async () => {
  const hub = $("hub").value.trim().replace(/\/+$/, ""), project = $("project").value.trim() || "default";
  if (!(await granted(hub))) return say($("status"), "That is not a web address.", "bad");
  await chrome.storage.local.set({ hub, project });
  say($("status"), "Testing…");
  const r = await ask({ type: "health" });
  if (!r.ok) return say($("status"), r.error, "bad");
  say($("status"), r.health.auth ? "Reached the hub. It has accounts: sign in below." : "Connected.", "ok");
};

$("login").onclick = async () => {
  const r = await ask({ type: "signin", username: $("u").value, password: $("p").value });
  $("p").value = "";
  if (!r.ok) return say($("lstatus"), r.error, "bad");
  say($("lstatus"), `Signed in as ${r.user.username} (${r.user.role}).`, "ok");
};
