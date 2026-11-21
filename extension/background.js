// The extension's service worker: the panel asks it to preview or run a scenario on a tab; it parses through the hub, runs it
// with the engine (lib/runner.js) and streams progress back. One run per tab at a time.
import { Run, canInject } from "./lib/runner.js";
import { hub, parse, health, signIn, settings, compileRecording, importChrome, saveToLibrary } from "./lib/hub.js";
import { Reporter } from "./lib/reporter.js";
import * as bridge from "./lib/bridge.js";
import { ServerRun } from "./lib/server-run.js";

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

// The hub runs it (its own runner and Playwright engine, in this tab, through the bridge): every capability of the server.
async function startOnServer({ text, data, sensitive, steps, tabId, config, sourceRun, name }) {
  const tab = tabId ? await chrome.tabs.get(tabId) : await target();
  if (!tab) throw new Error("Open the website you want to test in a tab first.");
  for (const r of runs.values()) if (r.tabIds.has(tab.id) && r.status === "running") throw new Error("A scenario is already running in this tab.");
  if (!canInject(tab.url)) throw new Error("Chrome doesn't let extensions run on this kind of page. Open a normal website (http or https) in the tab and try again.");
  let plan = steps, d = data || {}, sens = sensitive || [], notes = [], ignored = [];
  if (!plan) {                                                    // typed: the hub understands it first, so the panel can show the plan
    const parsed = await parse(text, dataFor(tab, data), sensitive || []);
    if (parsed.unparsed && parsed.unparsed.length) throw Object.assign(new Error("Some sentences were not understood: " + parsed.unparsed.join(" · ")), { unparsed: parsed.unparsed });
    plan = parsed.test.steps; d = parsed.test.data || {}; sens = parsed.test.sensitive || []; notes = parsed.notes || []; ignored = parsed.ignored || [];
  }
  const id = "r" + Date.now().toString(36) + (nextId++);
  const run = new ServerRun({ id, tabId: tab.id, onEvent: broadcast });
  const body = { name: name || undefined, config: { use_llm: true, record_video: true, timeout_ms: (config && config.timeout) || 15000 } };
  if (sourceRun && sourceRun.startsWith("library:")) { body.library = sourceRun.slice(8); body.data = d; body.sensitive = sens; }
  else if (sourceRun) { body.replay_run_id = sourceRun; body.data = d; body.sensitive = sens; }
  else { body.steps = plan; body.data = { ...dataFor(tab, {}), ...d }; body.sensitive = sens; }
  runs.set(id, run);
  try { await run.start(body, tab); } catch (e) { runs.delete(id); throw e; }
  setTimeout(() => runs.delete(id), 6 * 60 * 60 * 1000);
  run.log.push({ type: "report", runId: id, hubRun: run.hubRun });
  return { runId: id, hubRun: run.hubRun, total: plan.length, plan: plan.map((x) => ({ raw: x.raw, origin: x.origin || "", action: x.action })), notes, ignored };
}

async function start(msg) {
  const mode = (await chrome.storage.local.get({ engine: "server" })).engine;
  return mode === "builtin" ? startBuiltin(msg) : startOnServer(msg);
}

async function startBuiltin({ text, data, sensitive, steps, tabId, config, sourceRun, name }) {
  const tab = tabId ? await chrome.tabs.get(tabId) : await target();
  if (!tab) throw new Error("Open the website you want to test in a tab first.");
  for (const r of runs.values()) if (r.tabIds.has(tab.id) && r.status === "running") throw new Error("A scenario is already running in this tab.");
  const parsed = steps ? { test: { steps, data: data || {}, sensitive: sensitive || [] } } : await parse(text, dataFor(tab, data), sensitive || []);
  if (parsed.unparsed && parsed.unparsed.length) throw Object.assign(new Error("Some sentences were not understood: " + parsed.unparsed.join(" · ")), { unparsed: parsed.unparsed });
  const t = parsed.test;
  const id = "r" + Date.now().toString(36) + (nextId++);
  const all = { ...(t.data || {}), ...dataFor(tab, data) };
  // report the run to the hub (history, stats, live view, video, report, replayable script) unless that is switched off
  const rep = (await chrome.storage.local.get({ report: true })).report ? new Reporter() : null;
  if (rep) await rep.start({ name: name || t.name || (t.steps[0] && t.steps[0].raw) || "Extension run", steps: t.steps, data: all, sensitive: t.sensitive || [], url: tab.url, sourceRun });
  const onEvent = (ev) => {
    broadcast(ev);
    if (!rep) return;
    if (ev.type === "run_finished") rep.finish(ev).then((ok) => broadcast({ type: "report_done", runId: id, ok, hubRun: rep.id, error: rep.error }));
    else rep.event(ev);
  };
  const run = new Run({ id, tabId: tab.id, steps: t.steps, data: all, sensitive: t.sensitive || [], config, onEvent, onFrame: rep && rep.id ? (d, ts) => rep.frame(d, ts) : null });
  run.log.push({ type: "report", runId: id, hubRun: rep && rep.id, error: rep && !rep.id ? rep.error : null });
  runs.set(id, run);
  run.execute().finally(() => setTimeout(() => runs.delete(id), 30 * 60 * 1000));
  return { runId: id, hubRun: rep && rep.id, reportError: rep && !rep.id ? rep.error : null, total: t.steps.length, plan: t.steps.map((x) => ({ raw: x.raw, origin: x.origin || "", action: x.action })), notes: parsed.notes || [], ignored: parsed.ignored || [] };
}

// ------------------------------------------------------------------------------------------------ recording
// While recording, a content script (lib/recorder.js) runs in every frame of the tab and of any tab it opens. Events collect here
// (mirrored to session storage, so a sleeping service worker loses nothing); the hub turns them into steps.
let rec = null;
async function R() {
  if (!rec) { const s = await chrome.storage.session.get({ rec: null }).catch(() => ({ rec: null })); if (s.rec) rec = { ...s.rec, tabs: new Set(s.rec.tabs) }; }
  return rec;
}
const saveRec = () => rec && chrome.storage.session.set({ rec: { ...rec, tabs: [...rec.tabs] } }).catch(() => {});
const badge = (text) => { chrome.action.setBadgeText({ text }); if (text) chrome.action.setBadgeBackgroundColor({ color: "#dc2626" }); };

async function recStart() {
  const tab = await target();
  if (!tab || !canInject(tab.url)) throw new Error("Open the website you want to record in a tab first.");
  rec = { tabs: new Set([tab.id]), order: [tab.id], events: [{ type: "navigate", url: tab.url, tab: 1, t: 0 }], t0: Date.now(), active: true };
  await chrome.scripting.unregisterContentScripts({ ids: ["autopilot-rec"] }).catch(() => {});
  await chrome.scripting.registerContentScripts([{ id: "autopilot-rec", matches: ["<all_urls>"], js: ["lib/shared.js", "lib/recorder.js"], allFrames: true, runAt: "document_start", matchOriginAsFallback: true }]);
  await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["lib/shared.js", "lib/recorder.js"] });
  badge("REC"); saveRec();
  return { tab: { id: tab.id, title: tab.title, url: tab.url } };
}
async function recStop() {
  const r = await R(); if (!r) return { events: [] };
  r.active = false;
  await chrome.scripting.unregisterContentScripts({ ids: ["autopilot-rec"] }).catch(() => {});
  badge(""); saveRec();
  return { events: r.events };
}
function recPush(ev, tabId) {
  const i = rec.order.indexOf(tabId); if (i < 0) return;
  rec.events.push({ ...ev, tab: i + 1, t: (Date.now() - rec.t0) / 1000 });
  saveRec(); chrome.runtime.sendMessage({ type: "rec_changed", n: rec.events.length }).catch(() => {});
}
chrome.tabs.onCreated.addListener(async (t) => {
  const r = await R(); if (!r || !r.active || t.openerTabId == null || !r.tabs.has(t.openerTabId)) return;
  r.tabs.add(t.id); r.order.push(t.id); saveRec();
});
chrome.tabs.onUpdated.addListener(async (id, info) => {
  const r = await R(); if (!r || !r.active || !r.tabs.has(id) || !info.url || !canInject(info.url)) return;
  const last = [...r.events].reverse().find((e) => e.type === "navigate" && e.tab === r.order.indexOf(id) + 1);
  if (last && last.url === info.url) return;
  recPush({ type: "navigate", url: info.url }, id);
});

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
    case "rec-start": done(recStart()); return true;
    case "rec-stop": done(recStop()); return true;
    case "rec-state": done(R().then((r) => ({ active: !!(r && r.active), n: r ? r.events.length : 0, events: r ? r.events : [] }))); return true;
    case "rec-clear": rec = null; chrome.storage.session.remove("rec").catch(() => {}); badge(""); reply({ ok: true }); return false;
    case "rec-event": R().then((r) => { if (r && r.active && sender.tab && r.tabs.has(sender.tab.id)) recPush(msg.ev, sender.tab.id); }); return false;
    case "rec-check": done(R().then((r) => { if (!r) throw new Error("Not recording."); const last = r.events[r.events.length - 1]; r.events.push({ type: "assert_text", value: msg.text, tab: last ? last.tab : 1, t: (Date.now() - r.t0) / 1000 }); saveRec(); return { n: r.events.length }; })); return true;
    case "rec-selection": done((async () => {
      const r = await R(); const id = r && r.order[(r.events[r.events.length - 1] || { tab: 1 }).tab - 1];
      if (id == null) return { text: "" };
      const rs = await chrome.scripting.executeScript({ target: { tabId: id, allFrames: true }, func: () => String(getSelection() || "").trim() });
      return { text: (rs.map((x) => x.result).find(Boolean)) || "" };
    })()); return true;
    case "hub-lists": done(Promise.all([hub("/api/library").catch(() => []), hub("/api/runs?limit=15").catch(() => [])]).then(([library, runs]) => ({ library, runs }))); return true;
    case "replay-load": done(hub(msg.kind === "library" ? "/api/ext/replay/library/" + encodeURIComponent(msg.id) : "/api/ext/replay/run/" + encodeURIComponent(msg.id))); return true;
    case "bridge-session": done(bridge.session(msg.tabId)); return true;
    case "compile": done(compileRecording(msg.events, msg.name, msg.data, msg.sensitive)); return true;
    case "import-chrome": done(importChrome(msg.recording, msg.name)); return true;
    case "save-library": done(saveToLibrary(msg.name, msg.test)); return true;
    case "shadow": done(applyShadowSetting(!!msg.on).then(() => ({}))); return true;
    default: return false;
  }
});
