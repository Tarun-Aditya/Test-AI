export async function api(path, opts = {}) {
  const form = opts.body instanceof FormData;
  // X-Requested-With: the hub refuses writes without it (a page on another site can't add it: CSRF guard)
  const init = { ...opts, headers: { "X-Requested-With": "autopilot", ...(form ? {} : { "Content-Type": "application/json" }), ...(opts.headers || {}) } };
  if (init.body && !form && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const r = await fetch(path, init);
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("aiauto:signed-out"));
  if (!r.ok) {
    const d = j.detail;
    throw new Error(typeof d === "string" ? d : d && d.unparsed ? "Not understood: " + d.unparsed.join(" · ") : JSON.stringify(d || j));
  }
  return j;
}

export const art = (runId, path) => `/artifacts/${encodeURIComponent(runId)}/${path}`;

export const store = {
  get(k, d) { try { const v = localStorage.getItem("aiauto." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("aiauto." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

export const LIVE = ["queued", "dispatched", "running"];
export const isLive = (r) => LIVE.includes(r.status);

export function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export const secs = (ms) => (ms ? `${(ms / 1000).toFixed(1)}s` : "–");

/* dotted-path helpers for test data */
export function setPath(o, path, val) {
  const ks = path.split(".");
  let c = o;
  ks.slice(0, -1).forEach((k) => (c = c[k] = (typeof c[k] === "object" && c[k]) || {}));
  c[ks.at(-1)] = val;
}
export function flatten(o, p = "", out = []) {
  for (const [k, v] of Object.entries(o || {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && "sensitive" in v && "value" in v) out.push([p + k, String(v.value), !!v.sensitive]);
    else if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, p + k + ".", out);
    else out.push([p + k, Array.isArray(v) ? JSON.stringify(v) : String(v), false]);
  }
  return out;
}
