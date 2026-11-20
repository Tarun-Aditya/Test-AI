// Reports a run to the hub as it happens: the run appears in the hub's list (dashboards and stats count it), anyone watching sees
// it live, every step's screenshot is kept, the tab's screencast becomes the run's video, and when it ends the hub writes the
// report and a replayable script. A hub that can't be reached never stops the run: the run just isn't reported.
import { hub } from "./hub.js";

export class Reporter {
  constructor() { this.id = null; this.error = null; this.q = []; this.frames = []; this.chain = Promise.resolve(); this.lastFrameSent = 0; this.timer = null; this.failures = 0; }

  async start({ name, steps, data, sensitive, url, sourceRun }) {
    try {
      const r = await hub("/api/ext/runs", { method: "POST", body: { name, steps, data, sensitive, url, source_run: sourceRun || null } });
      this.id = r.run_id;
    } catch (e) { this.error = String(e.message || e); }
    return this.id;
  }

  // chain: one request at a time, in order (the hub builds the video after the last frame, then the report after the last step)
  _send(path, body) {
    this.chain = this.chain.then(() => hub(path, { method: "POST", body }).catch((e) => { this.failures++; this.error = String(e.message || e); }));
    return this.chain;
  }

  event(ev) {
    if (!this.id) return;
    const { runId, ...rest } = ev;
    this.q.push(rest);
    if (ev.type === "step_finished" || ev.type === "run_started") this.flushEvents();
    else if (!this.timer) this.timer = setTimeout(() => { this.timer = null; this.flushEvents(); }, 400);
  }
  flushEvents() {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (!this.id || !this.q.length) return this.chain;
    const events = this.q.splice(0);
    return this._send(`/api/ext/runs/${this.id}/events`, { events });
  }

  frame(data, t) {
    if (!this.id) return;
    this.frames.push({ t, data });
    if (this.frames.length >= 6 || t - this.lastFrameSent > 1000) this.flushFrames();
  }
  flushFrames() {
    if (!this.id || !this.frames.length) return this.chain;
    const frames = this.frames.splice(0); this.lastFrameSent = Date.now();
    return this._send(`/api/ext/runs/${this.id}/frames`, { frames });
  }

  // the run ended: everything sent, then the hub is told to write the report and video
  async finish(ev) {
    if (!this.id) return false;
    await this.flushFrames(); await this.flushEvents();
    this.q.push({ ...ev, runId: undefined, ended_ms: Date.now() });
    await this.flushEvents();
    await this.chain;
    return this.failures === 0;
  }
}
