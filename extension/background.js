// The extension's service worker: the panel asks it to preview or run a scenario on a tab; it parses through the hub, runs it
// with the engine (lib/runner.js) and streams progress back. One run per tab at a time.
import { Run, canInject } from "./lib/runner.js";
import { hub, parse, health, signIn, settings } from "./lib/hub.js";

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

// ------------------------------------------------------------------------------------------------ which tab?
// The panel automates the tab you are on. When the panel itself is open as a tab (or you are on an extension page), it is the
// website tab you looked at most recently. Only real pages count: a blank tab that is about to become something else does not.
const recent = [];                                           // website tab ids, most recent first
function seen(id, url) {
  if (!canInject(url)) return;
  const i = recent.indexOf(id); if (i >= 0) recent.splice(i, 1);
  recent.unshift(id); recent.length = Math.min(recent.length, 20);
}
chrome.tabs.onActivated.addListener(async ({ tabId }) => { try { const t = await chrome.tabs.get(tabId); seen(tabId, t.url); } catch (e) { /* gone */ } });
chrome.tabs.onUpdated.addListener((id, info, t) => { if (t.active && (info.url || info.status === "complete")) seen(id, t.url); });
chrome.tabs.onRemoved.addListener((id) => { const i = recent.indexOf(id); if (i >= 0) recent.splice(i, 1); });

async function target() {
  const [cur] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (cur && canInject(cur.url)) return cur;
  if (cur && /^(chrome|edge|about|view-source):/.test(cur.url || "")) return cur;      // a browser page: say so, don't guess another tab
  for (const id of recent) { try { const t = await chrome.tabs.get(id); if (canInject(t.url)) return t; } catch (e) { /* closed */ } }
  const all = await chrome.tabs.query({});
  return all.find((t) => canInject(t.url)) || null;
}

// ------------------------------------------------------------------------------------------------ runs
const runs = new Map();                 // runId -> Run
let nextId = 1;
const broadcast = (ev) => chrome.runtime.sendMessage(ev).catch(() => {});

function dataFor(tab, extra) {
  const u = new URL(tab.url);
  return { base_url: u.origin + u.pathname + u.search, ...extra };       // "the sign up page" is the page you are on
}

async function preview({ text, data, sensitive }) {
  const tab = await target();
  if (!tab) throw new Error("Open the website you want to test in a tab first.");
  const r = await parse(text, dataFor(tab, data), sensitive || []);
  return { tab: { id: tab.id, title: tab.title, url: tab.url, ok: canInject(tab.url) }, ...r };
}

async function start({ text, data, sensitive, steps, tabId, config }) {
  const tab = tabId ? await chrome.tabs.get(tabId) : await target();
  if (!tab) throw new Error("Open the website you want to test in a tab first.");
  for (const r of runs.values()) if (r.tabIds.has(tab.id) && r.status === "running") throw new Error("A scenario is already running in this tab.");
  const parsed = steps ? { test: { steps, data: data || {}, sensitive: sensitive || [] } } : await parse(text, dataFor(tab, data), sensitive || []);
  if (parsed.unparsed && parsed.unparsed.length) throw Object.assign(new Error("Some sentences were not understood: " + parsed.unparsed.join(" · ")), { unparsed: parsed.unparsed });
  const t = parsed.test;
  const id = "r" + Date.now().toString(36) + (nextId++);
  const run = new Run({ id, tabId: tab.id, steps: t.steps, data: { ...(t.data || {}), ...dataFor(tab, data) }, sensitive: t.sensitive || [], config, onEvent: broadcast });
  runs.set(id, run);
  run.execute().finally(() => setTimeout(() => runs.delete(id), 30 * 60 * 1000));
  return { runId: id, total: t.steps.length, plan: t.steps.map((x) => ({ raw: x.raw, origin: x.origin || "", action: x.action })), notes: parsed.notes || [], ignored: parsed.ignored || [] };
}

// Optional: open closed shadow roots on every page (a script that runs before the page's own). Off unless asked for in Settings.
async function applyShadowSetting(on) {
  const have = (await chrome.scripting.getRegisteredContentScripts({ ids: ["open-shadow"] }).catch(() => [])).length > 0;
  if (on && !have) await chrome.scripting.registerContentScripts([{ id: "open-shadow", matches: ["<all_urls>"], js: ["lib/open-shadow.js"], runAt: "document_start", world: "MAIN", allFrames: true, matchOriginAsFallback: true, persistAcrossSessions: true }]);
  if (!on && have) await chrome.scripting.unregisterContentScripts({ ids: ["open-shadow"] });
}
chrome.runtime.onInstalled.addListener(async () => applyShadowSetting((await chrome.storage.local.get({ openShadow: false })).openShadow));

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const done = (p) => p.then((v) => reply({ ok: true, ...v }), (e) => reply({ ok: false, error: String(e && e.message || e), unparsed: e && e.unparsed, status: e && e.status }));
  switch (msg.type) {
    case "target": done(target().then((t) => ({ tab: t && { id: t.id, title: t.title, url: t.url, ok: canInject(t.url) } }))); return true;
    case "preview": done(preview(msg)); return true;
    case "run": done(start(msg)); return true;
    case "stop": { const r = runs.get(msg.runId); if (r) r.cancel(); reply({ ok: true }); return false; }
    case "state": { const r = runs.get(msg.runId) || [...runs.values()].pop(); reply({ ok: true, runId: r && r.id, status: r && r.status, log: r ? r.log.filter((e) => e.type !== "dialog" || true) : [] }); return false; }
    case "health": done(health().then((h) => ({ health: h }))); return true;
    case "signin": done(signIn(msg.username, msg.password).then((u) => ({ user: u.user }))); return true;
    case "settings": done(settings().then((s) => ({ settings: s }))); return true;
    case "shadow": done(applyShadowSetting(!!msg.on).then(() => ({}))); return true;
    default: return false;
  }
});
