const $ = (id) => document.getElementById(id);
const ask = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: "No answer" })));
const say = (el, text, cls) => { el.textContent = text; el.className = cls || ""; };

async function renderAllowed() {
  const s = await chrome.storage.local.get({ allowedSites: [] });
  $("noallowed").hidden = s.allowedSites.length > 0;
  $("allowed").innerHTML = s.allowedSites.map((o, i) => `<li style="display:flex;justify-content:space-between;padding:3px 0"><span>${o.replace(/[<>&]/g, "")}</span><button class="ghost" data-i="${i}" aria-label="Stop allowing ${o.replace(/[<>&"]/g, "")}">Remove</button></li>`).join("");
}
$("allowed").onclick = async (e) => {
  const b = e.target.closest("button[data-i]"); if (!b) return;
  const s = await chrome.storage.local.get({ allowedSites: [] }); s.allowedSites.splice(+b.dataset.i, 1);
  await chrome.storage.local.set({ allowedSites: s.allowedSites }); renderAllowed();
};
$("ask").onchange = async () => { await chrome.storage.local.set({ askBeforeRunning: $("ask").checked }); say($("astatus"), $("ask").checked ? "On." : "Off: Autopilot will not ask.", "ok"); };

(async () => {
  const s = await chrome.storage.local.get({ hub: "http://localhost:8000", project: "default", askBeforeRunning: true });
  $("hub").value = s.hub; $("project").value = s.project; $("ask").checked = s.askBeforeRunning; renderAllowed();
})();

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
