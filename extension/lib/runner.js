// Runs a parsed scenario on a real tab: snapshot the page, ask the hub which element each step means, act with real browser
// input, check, and follow the scenario into any tab a step opens. Mirrors aiauto/runner.py, step for step.
import * as cdp from "./cdp.js";
import { locate } from "./hub.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => String(s == null ? "" : s).replace(/\s+/g, " ").trim().toLowerCase();
export class StepError extends Error {}

const ELEMENT_ACTIONS = new Set(["click", "double_click", "right_click", "type", "append", "hover", "select", "check", "uncheck", "upload", "drag", "assert_visible"]);
const WITH_TARGET = new Set(["press", "wait", "scroll", "assert_text", "assert_no_text"]);
const ACTS = new Set(["click", "double_click", "right_click", "type", "append", "press", "select", "check", "uncheck", "upload", "drag"]);   // may open a tab or navigate
const PASSIVE = new Set(["assert_text", "assert_no_text", "assert_visible", "assert_url", "assert_title", "wait", "screenshot"]);   // a held hover stays
const STOP = new Set("the a an of to in on at for and or button field link box input text element icon page click first second third last option checkbox radio select menu tab item".split(" "));
const NO_TEXT_SETTLE_MS = 2000;

// words the DOM walk may use to send fewer candidates on a big page (as the engines do)
const words = (target) => (String(target || "").toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => !STOP.has(w) && (w.length > 2 || /\d/.test(w)));

export function canInject(url) {
  return /^(https?|file|ftp):/i.test(url || "") && !/^https:\/\/(chrome\.google\.com\/webstore|chromewebstore\.google\.com)/i.test(url);
}

// tabs opened while a run is going (a click on target=_blank, window.open): the run follows them
const created = [];
chrome.tabs.onCreated.addListener((t) => { created.push({ id: t.id, opener: t.openerTabId, windowId: t.windowId, at: Date.now() }); if (created.length > 50) created.shift(); });

export class Run {
  constructor({ id, tabId, steps, data = {}, sensitive = [], config = {}, onEvent }) {
    this.id = id; this.steps = steps; this.data = data; this.sensitive = new Set(sensitive);
    this.cfg = { timeout: 10000, dialogs: "accept", highlight: true, screenshots: true, ...config };
    this.onEvent = onEvent; this.tabId = tabId; this.tabIds = new Set([tabId]); this.tabList = [tabId];
    this.cancelled = false; this.log = []; this.status = "running"; this.dialogPolicy = this.cfg.dialogs; this.lastDialog = "";
    this.held = null; this.map = new Map();
  }

  emit(type, extra = {}) {
    const ev = { type, runId: this.id, ...extra };
    this.log.push(ev);
    try { this.onEvent && this.onEvent(ev); } catch (e) { /* the panel may be closed */ }
  }
  cancel() { this.cancelled = true; }
  check() { if (this.cancelled) throw new StepError("Stopped"); }

  // ---------------------------------------------------------------------------------------------- test data
  fill(text) {
    if (text == null) return text;
    return String(text).replace(/\$\{\s*([\w.\-]+)\s*\}|\{\{\s*([\w.\-]+)\s*\}\}/g, (m, a, b) => {
      const key = a || b;
      const v = key.split(".").reduce((o, k) => (o == null ? o : o[k]), this.data);
      if (v === undefined || v === null || v === "") throw new StepError(`No test data for \${${key}}: add it under Data in the panel.`);
      return String(v);
    });
  }
  secretIn(text) {
    return [...String(text || "").matchAll(/\$\{\s*([\w.\-]+)\s*\}|\{\{\s*([\w.\-]+)\s*\}\}/g)].some((m) => this.sensitive.has(m[1] || m[2]));
  }
  mask(text) {                                  // what is shown: a secret never appears in the panel
    let t = String(text == null ? "" : text);
    for (const k of this.sensitive) { const v = this.data[k]; if (v) t = t.split(String(v)).join("••••••"); }
    return t;
  }

  // ---------------------------------------------------------------------------------------------- the run
  async execute() {
    this.emit("run_started", { total: this.steps.length, tab: await this.tabInfo(this.tabId) });
    const keep = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);          // keeps the service worker awake
    let failed = false;
    try {
      await this.prepareTab(this.tabId);
      for (let i = 0; i < this.steps.length; i++) {
        if (failed || this.cancelled) { this.emit("step_finished", { index: i, status: "skipped" }); continue; }
        failed = !(await this.runStep(i, this.steps[i]));
      }
      this.status = this.cancelled ? "cancelled" : failed ? "failed" : "passed";
    } catch (e) {
      this.status = "failed"; this.error = String(e.message || e);
    } finally {
      clearInterval(keep);
      for (const id of this.tabIds) await cdp.detach(id);
      this.emit("run_finished", { status: this.status, error: this.error || null, tabs: await Promise.all(this.tabList.map((t) => this.tabInfo(t))) });
    }
    return this.status;
  }

  async runStep(i, step) {
    const t0 = Date.now(), action = step.action;
    this.emit("step_started", { index: i, raw: this.mask(step.raw || step.action), action, target: step.target, value: this.secretIn(step.value) ? "••••••" : this.mask(step.value) });
    let status = "passed", error = null, picked = null;
    try {
      this.check();
      const live = { ...step, target: this.fill(step.target), value: this.fill(step.value) };
      const needsEl = ELEMENT_ACTIONS.has(action) || (step.target && WITH_TARGET.has(action));
      let loc = null;
      if (needsEl) {
        loc = await this.resolve(live, live.target);
        picked = loc.pickedBy;
        if (this.cfg.highlight) await this.call(loc, "box", [loc.uid, "located", `Step ${i + 1} · ${action.replace("_", " ")}`]);
      }
      if (this.held && PASSIVE.has(action)) await this.rehover();
      const since = Date.now();
      await this.perform(live, step, loc);
      if (action === "hover" && loc) this.held = loc; else if (!PASSIVE.has(action) && action !== "hover") this.held = null;
      if (loc && this.cfg.highlight) await this.call(loc, "box", [loc.uid, "done", `✓ Step ${i + 1}`]).catch(() => {});
      await this.settle(since, ACTS.has(action));
    } catch (e) {
      error = this.mask(String(e && e.message || e));
      status = step.optional ? "skipped" : "failed";
      if (step.optional) error = "optional step skipped: " + error;
    }
    let shot = null;
    if (this.cfg.screenshots) { try { shot = await cdp.screenshot(this.tabId); } catch (e) { /* tab gone */ } }
    this.emit("step_finished", { index: i, status, error, picked, ms: Date.now() - t0, shot, tab: this.tabList.indexOf(this.tabId) + 1, url: (await this.tabInfo(this.tabId)).url });
    return status !== "failed";
  }

  // ---------------------------------------------------------------------------------------------- tabs
  async tabInfo(id) {
    try { const t = await chrome.tabs.get(id); return { id, title: t.title || "", url: t.url || t.pendingUrl || "" }; }
    catch (e) { return { id, title: "(closed)", url: "" }; }
  }

  async prepareTab(tabId) {
    const t = await chrome.tabs.get(tabId);
    if (!canInject(t.url)) throw new StepError("Chrome doesn't let extensions run on this kind of page. Open a normal website (http or https) in the tab and try again.");
    await cdp.attach(tabId, (d) => { this.lastDialog = d.message || ""; this.emit("dialog", { message: d.message, kind: d.type }); return this.dialogPolicy !== "dismiss"; });
    try { await chrome.tabs.update(tabId, { active: true }); } catch (e) { /* ok */ }
  }

  async follow(newId) {                          // the scenario continues in the tab a step opened
    this.tabId = newId; this.tabIds.add(newId); if (!this.tabList.includes(newId)) this.tabList.push(newId);
    await this.waitLoaded(newId);
    await this.prepareTab(newId);
    this.held = null; this.map.clear();
    this.emit("tab", { index: this.tabList.indexOf(newId) + 1, tab: await this.tabInfo(newId), why: "a step opened a new tab: the scenario continues there" });
  }

  async waitLoaded(tabId, timeout = 15000) {
    const end = Date.now() + timeout; let steady = 0, blankSince = Date.now();
    while (Date.now() < end) {
      let t; try { t = await chrome.tabs.get(tabId); } catch (e) { throw new StepError("The tab was closed"); }
      const url = t.url || t.pendingUrl || "";
      const blank = !url || url === "about:blank" || url === "chrome://newtab/";
      if (t.status === "complete" && (!blank || Date.now() - blankSince > 2500)) { if (++steady >= 2) return t; } else steady = 0;
      await sleep(120);
    }
    throw new StepError("The page didn't finish loading in time");
  }

  // After a step that can navigate or open a tab: follow a new tab, wait for the page to settle.
  async settle(since, acts) {
    await sleep(acts ? 350 : 80);
    const known = (id) => this.tabIds.has(id);
    const fresh = created.filter((c) => c.at >= since - 30 && !known(c.id) && (known(c.opener) || c.opener == null)).pop();
    if (fresh) { try { await chrome.tabs.get(fresh.id); await this.follow(fresh.id); return; } catch (e) { /* closed already */ } }
    try { await this.waitLoaded(this.tabId, Math.min(this.cfg.timeout, 15000)); } catch (e) { if (!(e instanceof StepError)) throw e; if (/closed/.test(e.message)) await this.recoverTab(); else throw e; }
  }

  async recoverTab() {                           // the tab we were in closed itself: carry on in the one before it
    this.tabIds.delete(this.tabId);
    for (const id of [...this.tabList].reverse()) {
      if (this.tabIds.has(id)) { try { await chrome.tabs.get(id); this.tabId = id; await this.prepareTab(id); this.emit("tab", { index: this.tabList.indexOf(id) + 1, tab: await this.tabInfo(id), why: "the tab closed: back to the previous one" }); return; } catch (e) { /* gone */ } }
    }
    throw new StepError("Every tab of this run was closed");
  }

  // ---------------------------------------------------------------------------------------------- page access
  async inject(tabId) {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, world: "MAIN", files: ["lib/shared.js", "lib/page.js"] });
  }

  async snapshot(wordList) {
    await this.inject(this.tabId);
    const res = await chrome.scripting.executeScript({ target: { tabId: this.tabId, allFrames: true }, world: "MAIN", func: (w) => (window.__aiautoSnapshot ? window.__aiautoSnapshot(w) : []), args: [wordList] });
    const out = []; this.map.clear();
    res.sort((a, b) => a.frameId - b.frameId).forEach((r, n) => {
      const off = 100000 * n;
      for (const c of r.result || []) {
        const local = c.uid; c.uid = off + local; c.anc = (c.anc || []).map((a) => off + a);
        this.map.set(c.uid, { frameId: r.frameId, local, tag: c.tag, type: c.type, draggable: c.draggable, options: c.options });
        out.push(c);
      }
    });
    return out;
  }

  // run a page.js function in the element's own frame
  async call(loc, fn, args = []) {
    const [r] = await chrome.scripting.executeScript({ target: { tabId: this.tabId, frameIds: [loc.frameId] }, world: "MAIN", func: (f, a) => window.__aiautoPage && window.__aiautoPage[f](...a), args: [fn, args] });
    return r && r.result;
  }
  async callTop(fn, args = []) {                // in every frame, results joined (page text)
    await this.inject(this.tabId);
    const rs = await chrome.scripting.executeScript({ target: { tabId: this.tabId, allFrames: true }, world: "MAIN", func: (f, a) => window.__aiautoPage && window.__aiautoPage[f](...a), args: [fn, args] });
    return rs.map((r) => r.result);
  }
  async pageText() { return (await this.callTop("pageText")).filter(Boolean).join("\n"); }

  // ---------------------------------------------------------------------------------------------- find the element
  async resolve(step, target) {
    if (!target) throw new StepError("This step needs an element but has no description of it.");
    const deadline = Date.now() + this.cfg.timeout, probe = { action: step.action, target, value: step.value, nth: step.nth, raw: step.raw };
    let filter = step.nth == null ? words(target) : [], ties = 0, last = null;
    for (;;) {
      this.check();
      let cands = [];
      try { cands = await this.snapshot(filter); } catch (e) { if (/Cannot access|showing error page|No tab/i.test(String(e.message))) { await sleep(300); continue; } throw e; }
      const r = cands.length ? await locate(probe, cands, target) : { uid: null, problem: "the page has no elements yet", near: [] };
      last = r;
      if (r.uid != null) {
        const m = this.map.get(r.uid);
        return { uid: m.local, frameId: m.frameId, local: false, tag: m.tag, type: m.type, draggable: m.draggable, pickedBy: r.picked_by, options: m.options };
      }
      if (r.problem && /match equally well/.test(r.problem) && ++ties >= 2) throw new StepError(`Ambiguous '${target}': ${r.problem}`);
      if (filter.length) { filter = []; continue; }             // nothing among the pre-filtered ones: look at everything
      if (Date.now() > deadline) break;
      await sleep(500);
    }
    const near = (last.near || []).map((n) => `${n.what} (score ${n.score})`).join("; ");
    throw new StepError(`Could not identify element for '${target}'. Closest: ${near || "none"}.`);
  }

  // ---------------------------------------------------------------------------------------------- acting
  async actionable(loc, needEnabled = true) {
    const deadline = Date.now() + this.cfg.timeout; let why = "";
    for (;;) {
      this.check();
      const p = await this.call(loc, "prep", [loc.uid]);
      if (!p || !p.ok) throw new StepError("The element went away before it could be used.");
      loc.local = !!p.local;
      if (!p.hidden && p.free && !(needEnabled && p.disabled)) {
        await sleep(60);
        const q = await this.call(loc, "prep", [loc.uid]);          // still? (animations, lazy layout)
        if (q && q.ok && Math.abs(q.x - p.x) < 1 && Math.abs(q.y - p.y) < 1) return q;
      } else why = p.hidden ? "it is not visible" : p.disabled && needEnabled ? "it is disabled" : `${p.covering} is covering it`;
      if (Date.now() > deadline) throw new StepError(`Can't act on the element: ${why || "it keeps moving"}.`);
      await sleep(150);
    }
  }

  async click(loc, { button = "left", count = 1 } = {}) {
    const p = await this.actionable(loc);
    if (p.local) await this.call(loc, "clickDom", [loc.uid, count, button === "right"]);       // a cross-origin frame: act from inside it
    else await cdp.click(this.tabId, p.x, p.y, { button, count });
  }

  async rehover() {
    try { const p = await this.actionable(this.held, false); if (!p.local) await cdp.move(this.tabId, p.x, p.y); } catch (e) { this.held = null; }
  }

  async type(loc, text, { append = false, secret = false } = {}) {
    const p = await this.actionable(loc);
    if (secret) await this.call(loc, "maskField", [loc.uid]);
    const special = ["date", "time", "datetime-local", "month", "week", "color", "range", "file"].includes(p.type) || p.tag === "select";
    if (p.local || special) { await this.call(loc, "setValue", [loc.uid, text, append]); return; }
    await this.call(loc, append ? "focusEnd" : "focusAll", [loc.uid]);
    if (text === "") { if (!append) await cdp.press(this.tabId, "Delete"); return; }
    await cdp.insertText(this.tabId, text);
  }

  async perform(live, step, loc) {
    const a = live.action, v = live.value;
    switch (a) {
      case "navigate": {
        if (!v) throw new StepError("No address to open.");
        const cur = (await chrome.tabs.get(this.tabId)).url || "";
        if (sameAddress(cur, v)) { this.emit("note", { message: "Already on that page: nothing to open." }); return; }
        await chrome.tabs.update(this.tabId, { url: v });
        return;
      }
      case "back": await chrome.tabs.goBack(this.tabId); return;
      case "refresh": await chrome.tabs.reload(this.tabId); return;
      case "switch_tab": {
        const list = this.tabList.filter((id) => this.tabIds.has(id));
        const id = v === "last" || v == null ? list[list.length - 1] : list[parseInt(v, 10)] ?? list[list.length - 1];
        if (id == null) throw new StepError("No such tab in this run.");
        if (id !== this.tabId) { this.tabId = id; await this.prepareTab(id); await chrome.tabs.update(id, { active: true }); this.held = null; this.map.clear(); this.emit("tab", { index: this.tabList.indexOf(id) + 1, tab: await this.tabInfo(id), why: "switched" }); }
        return;
      }
      case "click": return this.click(loc);
      case "double_click": return this.click(loc, { count: 2 });
      case "right_click": return this.click(loc, { button: "right" });
      case "type": case "append": {
        if (loc.tag === "select") return this.choose(loc, [v]);
        return this.type(loc, v, { append: a === "append", secret: this.secretIn(step.value) });
      }
      case "press": {
        if (loc) { const p = await this.actionable(loc, false); await this.call(loc, p.tag === "input" || p.tag === "textarea" ? "focusEnd" : "focusAll", [loc.uid]); }
        return cdp.press(this.tabId, v || "Enter");
      }
      case "hover": { const p = await this.actionable(loc, false); if (p.local) await this.call(loc, "hoverDom", [loc.uid]); else await cdp.move(this.tabId, p.x, p.y); await sleep(400); return; }
      case "select": return this.choose(loc, String(v || "").split("|").map((s) => s.trim()).filter(Boolean));
      case "check": case "uncheck": {
        const want = a === "check";
        if ((await this.call(loc, "checked", [loc.uid])) !== want) await this.click(loc);
        if ((await this.call(loc, "checked", [loc.uid])) !== want) throw new StepError(`Couldn't ${a} it: it is still ${want ? "unchecked" : "checked"}.`);
        return;
      }
      case "upload": throw new StepError("Uploading a file isn't supported in the extension yet. Use the app for steps that attach files.");
      case "drag": return this.drag(loc, live, step);
      case "scroll": return this.call(loc || { frameId: 0 }, "scroll", [v || "down", loc ? loc.uid : null]);
      case "wait": { if (!loc) { const end = Date.now() + 1000 * parseFloat(v || 1); while (Date.now() < end) { this.check(); await sleep(Math.min(300, end - Date.now())); } } return; }
      case "pause": {
        const secs = parseFloat(v || 30); this.emit("paused", { seconds: secs, message: "Do the manual step (a login, a code) in the page, then wait." });
        const end = Date.now() + secs * 1000; while (Date.now() < end) { this.check(); await sleep(400); } return;
      }
      case "dialog": this.dialogPolicy = /^(dismiss|cancel|reject)/i.test(v || "") ? "dismiss" : "accept"; return;
      case "screenshot": return;
      case "assert_visible": if (!(await this.call(loc, "isVisible", [loc.uid]))) throw new StepError(`'${live.target}' exists but is not visible.`); return;
      case "assert_text": case "assert_url": case "assert_title": case "assert_dialog": return this.expect(a, live, loc);
      case "assert_no_text": return this.expectAbsent(live, loc);
      default: throw new StepError(`The extension doesn't support '${a}' yet.`);
    }
  }

  async choose(loc, options) {
    if (loc.tag === "select") {
      const r = await this.call(loc, "selectOption", [loc.uid, options]);
      if (!r || !r.ok) throw new StepError(`No option ${options.map((o) => `'${o}'`).join(", ")} in that list. It has: ${(r && r.options || []).slice(0, 12).join(", ")}.`);
      return;
    }
    for (const opt of options) {                // a custom dropdown: open it, then click the option by its text
      await this.click(loc); await sleep(300);
      const o = await this.resolve({ action: "click", target: opt }, opt);
      await this.click(o);
    }
  }

  async drag(loc, live, step) {
    if (!live.value) throw new StepError("Drag needs a destination.");
    const dst = await this.resolve({ action: "click", target: live.value }, live.value);
    const a = await this.actionable(loc, false), b = await this.actionable(dst, false);
    if (a.local || b.local) throw new StepError("Dragging inside a cross-origin frame isn't supported.");
    await cdp.drag(this.tabId, a, b);
  }

  async expect(a, live, loc) {
    const want = String(live.value || "").trim(), end = Date.now() + this.cfg.timeout; let got = "";
    for (;;) {
      this.check();
      try {
        const tab = await chrome.tabs.get(this.tabId);
        got = a === "assert_url" ? tab.url : a === "assert_title" ? tab.title : a === "assert_dialog" ? this.lastDialog : loc ? await this.call(loc, "text", [loc.uid]) : await this.pageText();
      } catch (e) { got = got || ""; }
      if (norm(got).includes(norm(want))) return;
      if (Date.now() > end) break;
      if (this.held) await this.rehover();                    // a hover that was taken away: hover again
      await sleep(250);
    }
    const what = { assert_url: "URL", assert_title: "title", assert_dialog: "dialog" }[a] || (loc ? "element text" : "page");
    const i = norm(got).indexOf(norm(want));
    throw new StepError(`Expected ${what} to contain '${want}'; got '${String(got).replace(/\s+/g, " ").slice(0, 160)}'${i < 0 ? "" : ""}`);
  }

  async expectAbsent(live, loc) {
    const unwanted = String(live.value || "").trim(), end = Date.now() + Math.min(this.cfg.timeout, NO_TEXT_SETTLE_MS);
    for (;;) {
      this.check();
      let got = ""; try { got = loc ? await this.call(loc, "text", [loc.uid]) : await this.pageText(); } catch (e) { /* retry */ }
      if (unwanted && norm(got).includes(norm(unwanted))) throw new StepError(`Expected ${loc ? "element text" : "page"} NOT to contain '${unwanted}'.`);
      if (Date.now() > end) return;
      await sleep(300);
    }
  }
}

// the same page, ignoring a #fragment and a trailing slash
function sameAddress(a, b) {
  const clean = (u) => { try { const x = new URL(u); x.hash = ""; return (x.origin + x.pathname.replace(/\/+$/, "") + x.search).toLowerCase(); } catch (e) { return String(u).replace(/#.*/, "").replace(/\/+$/, "").toLowerCase(); } };
  return clean(a) === clean(b);
}
