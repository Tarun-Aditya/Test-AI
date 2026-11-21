// The extension as a browser for the hub's own engine. It holds a WebSocket to the hub and, for the tab(s) being driven, relays the
// DevTools protocol both ways through Chrome's debugger API. It decides nothing: the hub's Playwright engine and runner do the
// finding, acting, waiting and healing; here a command arrives, Chrome runs it, the answer goes back.
import { hub, settings } from "./hub.js";

let ws = null, link = null, linkBase = "", connecting = null, pingTimer = null;
const order = [];                                   // tab ids in the order the hub's run met them (tab 1, tab 2, …)
export const attachedOrder = () => order.slice();
const attached = new Set();                         // tab ids the debugger is attached to for the hub
export const runListeners = new Set();              // (event) => void: progress of the runs the hub executes in our tabs
let screencast = false;                             // stream each driven tab's picture to the hub (the run's video)

const send = (obj) => { try { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); } catch (e) { /* closing */ } };

export async function connect() {
  const cur = (await settings()).hub;
  if (ws && ws.readyState === 1 && linkBase === cur) return link;
  if (ws && linkBase !== cur) { const old = ws; ws = null; link = null; try { old.close(); } catch (e) { /* gone */ } }      // the hub address changed
  if (connecting) return connecting;
  connecting = (async () => {
    const r = await hub("/api/ext/bridge", { method: "POST", body: {} });
    const { hub: base } = await settings();
    const url = base.replace(/^http/, "ws") + "/ws/ext?link=" + encodeURIComponent(r.link);
    await new Promise((res, rej) => {
      const s = new WebSocket(url);
      s.onopen = () => { ws = s; res(); };
      s.onerror = () => rej(new Error("Can't open the live connection to the hub at " + base));
    });
    link = r.link; linkBase = base;
    ws.onmessage = (m) => onMessage(m).catch(() => {});
    ws.onclose = () => { ws = null; link = null; clearInterval(pingTimer); releaseAll(); };
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ ping: Date.now() }), 20000);      // traffic keeps the service worker alive
    return link;
  })().finally(() => { connecting = null; });
  return connecting;
}

export async function session(tabId) {
  const l = await connect();
  order.length = 0;                                 // a new run: tab numbering starts again
  return hub("/api/ext/session", { method: "POST", body: { link: l, tab_id: tabId } });
}

async function startScreencast(tabId) {
  try { await chrome.debugger.sendCommand({ tabId }, "Page.startScreencast", { format: "jpeg", quality: 60, maxWidth: 1280, maxHeight: 800, everyNthFrame: 1 }); } catch (e) { /* no picture */ }
}

async function attach(tabId) {
  if (attached.has(tabId)) { if (screencast) startScreencast(tabId); return; }
  try { await chrome.debugger.attach({ tabId }, "1.3"); }
  catch (e) { if (!/already attached/i.test(String(e.message))) throw new Error("Chrome would not let the extension attach to this tab: " + e.message + " (close DevTools on it, or use a normal http(s) page)"); }
  attached.add(tabId);
  if (!order.includes(tabId)) order.push(tabId);
  if (screencast) startScreencast(tabId);
  try { const t = await chrome.tabs.get(tabId); await chrome.windows.update(t.windowId, { focused: true }); await chrome.tabs.update(tabId, { active: true }); } catch (e) { /* ok */ }   // a background tab does not answer
}
async function releaseAll() {
  for (const id of [...attached]) { try { await chrome.debugger.detach({ tabId: id }); } catch (e) { /* closed */ } }
  attached.clear();
}

async function onMessage(m) {
  const msg = JSON.parse(m.data);
  if (msg.event === "run") { for (const f of runListeners) { try { f(msg.ev); } catch (e) { /* a listener failed */ } } return; }
  if (msg.id == null) return;
  const reply = (result) => send({ id: msg.id, result: result || {} });
  const fail = (e) => send({ id: msg.id, error: { message: String((e && e.message) || e) } });
  const p = msg.params || {};
  try {
    switch (msg.method) {
      case "attach": screencast = !!p.screencast; await attach(p.tabId); return reply();
      case "release": await releaseAll(); return reply();
      case "tabInfo": { const t = await chrome.tabs.get(p.tabId); return reply({ url: t.url || "", pending: t.pendingUrl || "", title: t.title || "", status: t.status || "" }); }
      case "cdp": { const r = await chrome.debugger.sendCommand({ tabId: p.tabId, ...(p.sessionId ? { sessionId: p.sessionId } : {}) }, p.cdpMethod, p.cdpParams || {}); return reply(r); }
      case "createTab": { const t = await chrome.tabs.create({ url: p.url || "about:blank", active: true }); await attach(t.id); return reply({ tabId: t.id, url: t.url || t.pendingUrl || "" }); }
      case "closeTab": attached.delete(p.tabId); await chrome.tabs.remove(p.tabId).catch(() => {}); return reply();
      case "activateTab": await chrome.tabs.update(p.tabId, { active: true }); return reply();
      default: return fail("Unknown request " + msg.method);
    }
  } catch (e) { fail(e); }
}

chrome.debugger.onEvent.addListener((src, method, params) => {
  if (!attached.has(src.tabId)) return;
  if (method === "Page.screencastFrame" && !src.sessionId) {                 // ours, not the engine's: to the hub as video, acknowledged here
    send({ event: "frame", tabId: src.tabId, t: Date.now(), data: params.data });
    chrome.debugger.sendCommand({ tabId: src.tabId }, "Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
    return;
  }
  send({ event: "cdp", tabId: src.tabId, ...(src.sessionId ? { sessionId: src.sessionId } : {}), method, params });
});
export const detachListeners = new Set();           // (tabId, reason) => void: called when the person closes Chrome's "debugging this browser" bar
chrome.debugger.onDetach.addListener((src, reason) => {
  if (!attached.delete(src.tabId)) return;
  for (const f of detachListeners) { try { f(src.tabId, reason); } catch (e) { /* a listener failed */ } }
  send({ event: "tab", kind: "removed", tabId: src.tabId });
});
// a tab the driven tab opens (target=_blank, window.open): driven too
chrome.tabs.onCreated.addListener(async (t) => {
  if (!ws || t.openerTabId == null || !attached.has(t.openerTabId)) return;
  try { await attach(t.id); send({ event: "tab", kind: "created", tabId: t.id, openerTabId: t.openerTabId, url: t.pendingUrl || t.url || "", title: t.title || "" }); } catch (e) { /* not drivable */ }
});
chrome.tabs.onRemoved.addListener((id) => { if (attached.delete(id)) send({ event: "tab", kind: "removed", tabId: id }); });
chrome.tabs.onUpdated.addListener((id, info, t) => { if (attached.has(id) && (info.url || info.title)) send({ event: "tab", kind: "info", tabId: id, url: t.url, title: t.title }); });
