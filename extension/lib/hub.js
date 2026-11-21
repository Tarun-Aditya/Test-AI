// The extension's link to the hub: the same REST API the web app uses. The hub does the thinking (parsing English and
// business paragraphs, AI, ranking elements, keeping secrets); the extension does the doing, in the page.
export class HubError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function settings() {
  const s = await chrome.storage.local.get({ hub: "http://localhost:8000", project: "default" });
  return { hub: String(s.hub || "").replace(/\/+$/, ""), project: s.project || "default" };
}

export async function hub(path, { method = "GET", body } = {}) {
  const { hub: base, project } = await settings();
  let r;
  try {
    r = await fetch(base + path, {
      method, credentials: "include",
      headers: { "X-Requested-With": "autopilot", "X-Project": project, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new HubError(`Can't reach the hub at ${base}. Start it (./start.sh) or set its address in the extension options.`, 0);
  }
  if (r.status === 401) throw new HubError("Sign in to the hub first (extension options).", 401);
  if (!r.ok) {
    let msg = "";
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || JSON.stringify(j.detail); } catch (e) { /* not JSON */ }
    const err = new HubError(msg || `The hub answered ${r.status}`, r.status);
    try { err.detail = (await r.clone().json()).detail; } catch (e) { /* no detail */ }
    throw err;
  }
  return r.json();
}

export const health = () => hub("/api/health");
export const signIn = (username, password) => hub("/api/auth/login", { method: "POST", body: { username, password } });
export const parse = (text, data, sensitive, useLlm = true) =>
  hub("/api/parse", { method: "POST", body: { text, data, sensitive, use_llm: useLlm } });
export const compileRecording = (events, name, data, sensitive) =>
  hub("/api/record/compile", { method: "POST", body: { events, name, data: data || {}, sensitive: sensitive || [] } });
export const importChrome = (recording, name) => hub("/api/record/import-chrome", { method: "POST", body: { recording, name } });
export const saveToLibrary = (name, test) => hub("/api/library", { method: "POST", body: { name, test } });
