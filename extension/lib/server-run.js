// A run the HUB executes in this tab: the hub's own runner and Playwright engine drive the tab through the bridge (lib/bridge.js).
// This class only starts it, relays the hub's progress to the panel in the shape the panel already shows, and stops it on request.
import { hub, settings } from "./hub.js";
import * as bridge from "./bridge.js";

const toDataUrl = async (blob) => {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return `data:${blob.type || "image/png"};base64,${btoa(s)}`;
};

export class ServerRun {
  constructor({ id, tabId, onEvent }) {
    this.id = id; this.tabId = tabId; this.onEvent = onEvent; this.tabIds = new Set([tabId]); this.tabList = [tabId];
    this.status = "running"; this.log = []; this.hubRun = null; this.chain = Promise.resolve(); this.knownTabs = 1; this.listener = null;
  }

  emit(type, extra = {}) {
    const ev = { type, runId: this.id, ...extra };
    this.log.push(ev);
    try { this.onEvent && this.onEvent(ev); } catch (e) { /* the panel may be closed */ }
  }

  async tabInfo(id) {
    try { const t = await chrome.tabs.get(id); return { id, title: t.title || "", url: t.url || "" }; } catch (e) { return { id, title: "(closed)", url: "" }; }
  }

  // body: the POST /api/runs request (without the extension session, which is added here)
  async start(body, tab) {
    const session = await bridge.session(tab.id);
    const early = [];                                      // events can arrive before the POST's answer tells us the run's id
    this.listener = (ev) => {
      if (!this.hubRun) { early.push(ev); return; }
      if (ev.run_id === this.hubRun) this.chain = this.chain.then(() => this.translate(ev)).catch(() => {});
    };
    bridge.runListeners.add(this.listener);
    try {
      const r = await hub("/api/runs", { method: "POST", body: { ...body, ext_session: session.token } });
      this.hubRun = r.run_ids[0];
    } catch (e) { bridge.runListeners.delete(this.listener); throw e; }
    this.tabList = [tab.id];
    for (const ev of early) this.listener(ev);
    return this.hubRun;
  }

  async cancel() { if (this.hubRun) try { await hub(`/api/runs/${encodeURIComponent(this.hubRun)}/cancel`, { method: "POST", body: {} }); } catch (e) { /* already over */ } }

  async translate(ev) {
    switch (ev.type) {
      case "run_started": this.emit("run_started", { total: ev.total, tab: await this.tabInfo(this.tabId) }); break;
      case "step_started": this.emit("step_started", { index: ev.index, raw: ev.raw, action: ev.action, target: ev.target, value: ev.value }); break;
      case "step_finished": {
        if (ev.tab && ev.tab > this.knownTabs) {                           // the run followed a step into a new tab
          const ids = bridge.attachedOrder(); this.knownTabs = ev.tab;
          const id = ids[ev.tab - 1]; if (id != null) { this.tabList[ev.tab - 1] = id; this.tabIds.add(id); this.emit("tab", { index: ev.tab, tab: await this.tabInfo(id), why: "a step opened a new tab: the run continues there" }); }
        }
        let shot = null;
        if (ev.screenshot && this.hubRun) {
          try { const { hub: base } = await settings(); const r = await fetch(`${base}/artifacts/${encodeURIComponent(this.hubRun)}/${ev.screenshot}`, { credentials: "include" }); if (r.ok) shot = await toDataUrl(await r.blob()); } catch (e) { /* no picture */ }
        }
        this.emit("step_finished", { index: ev.index, status: ev.status, error: ev.error || null, shot, ms: ev.duration_ms, picked: ev.picked_by, url: ev.url, tab: ev.tab || 1 });
        break;
      }
      case "run_finished": {
        this.status = ev.status || "failed";
        const tabs = await Promise.all(this.tabList.filter((x) => x != null).map((id) => this.tabInfo(id)));
        bridge.runListeners.delete(this.listener);
        this.emit("run_finished", { status: this.status, error: ev.error || null, tabs });
        this.emit("report_done", { ok: true, hubRun: this.hubRun, error: null });
        break;
      }
    }
  }
}
