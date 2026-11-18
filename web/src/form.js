import { flatten, setPath, store } from "./api.js";

export const BROWSERS = [
  ["chromium", "Chromium (bundled)"], ["chrome", "Google Chrome"], ["msedge", "Microsoft Edge"],
  ["firefox", "Firefox"], ["webkit", "WebKit (Playwright only)"],
];

export const DEFAULT_FORM = {
  text: "", name: "", steps: null, stepsText: null, stepsParser: null, srcMode: "text", libName: "",
  dataMode: "table", rows: [{ k: "", v: "", s: false }], iterCols: [], iterRows: [], json: "", sensKeys: "",
  engine: "playwright", agent: "local", headless: true,
  browser: "chromium", timeout: 10000, dialogs: "accept", slowmo: 0,
  video: true, shots: true, live: true, highlight: true, fullpage: false, heal: true, useLlm: true,
  session: "", saveSession: "", profileDir: "", profileName: "",
};

/** Persist everything except sensitive values (those never touch browser storage). */
export function saveForm(f) {
  const sensCols = new Set(f.iterCols.filter((c) => c.sensitive).map((c) => c.key));
  store.set("form2", {
    ...f,
    rows: f.rows.map((r) => (r.s ? { ...r, v: "" } : r)),
    iterRows: f.iterRows.map((r) => ({ ...r, values: Object.fromEntries(Object.entries(r.values).map(([k, v]) => [k, sensCols.has(k) ? "" : v])) })),
    json: "",
  });
}
export const loadForm = () => ({ ...DEFAULT_FORM, ...store.get("form2", {}) });

export function tableData(rows) {
  const data = {}, sensitive = [];
  rows.forEach((r) => {
    const k = r.k.trim();
    if (!k) return;
    let v = r.v;
    try { if (/^[[{]/.test(v.trim())) v = JSON.parse(v); } catch { /* keep as text */ }
    setPath(data, k, v);
    if (r.s) sensitive.push(k);
  });
  return { data, sensitive };
}

export function iterationsOf(f) {
  return f.iterRows.filter((r) => r.on).map((r, i) => {
    const data = {};
    f.iterCols.forEach((c) => { if (c.key.trim()) setPath(data, c.key.trim(), r.values[c.key] ?? ""); });
    return { data, browser: r.browser || null, label: `#${i + 1}${r.browser ? " " + r.browser : ""}` };
  });
}

/** What the API needs: {data, sensitive, iterations?} */
export function dataPayload(f) {
  if (f.dataMode === "json") {
    const t = f.json.trim();
    let data = null;
    if (t) { try { data = JSON.parse(t); } catch (e) { throw new Error("Test data is not valid JSON: " + e.message); } }
    return { data, sensitive: f.sensKeys.split(",").map((s) => s.trim()).filter(Boolean) };
  }
  const shared = tableData(f.rows);
  if (f.dataMode === "iter") {
    const its = iterationsOf(f);
    if (!its.length) throw new Error("Add at least one enabled iteration, or switch to Single run");
    return { ...shared, sensitive: [...shared.sensitive, ...f.iterCols.filter((c) => c.sensitive).map((c) => c.key.trim())], iterations: its };
  }
  return shared;
}

/** JSON text -> table rows / iterations (when switching away from the JSON editor). */
export function fromJson(f) {
  let obj = {};
  try { obj = f.json.trim() ? JSON.parse(f.json) : {}; } catch (e) { throw new Error("Data JSON is invalid: " + e.message); }
  const sens = new Set(f.sensKeys.split(",").map((s) => s.trim()).filter(Boolean));
  const rowsData = Array.isArray(obj.rows) ? obj.rows : null;
  delete obj.rows;
  const rows = flatten(obj).map(([k, v, s]) => ({ k, v, s: s || sens.has(k) }));
  const out = { rows: rows.length ? rows : [{ k: "", v: "", s: false }] };
  if (rowsData) {
    const keys = [...new Set(rowsData.flatMap((r) => flatten(r).map((x) => x[0])))].filter((k) => k !== "__browser");
    out.iterCols = keys.map((k) => ({ key: k, sensitive: sens.has(k) }));
    out.iterRows = rowsData.map((r) => ({ on: true, browser: r.__browser || "", values: Object.fromEntries(flatten(r).map(([k, v]) => [k, v])) }));
  }
  return out;
}

export function toJson(f) {
  const p = (() => { try { return dataPayload(f); } catch { return tableData(f.rows); } })();
  const j = { ...(p.data || {}) };
  if (p.iterations) j.rows = p.iterations.map((it) => ({ ...it.data, ...(it.browser ? { __browser: it.browser } : {}) }));
  return { json: JSON.stringify(j, null, 2), sensKeys: p.sensitive.join(", ") };
}

export function runConfig(f) {
  return {
    engine: f.engine, browser: f.browser, headless: f.headless, record_video: f.video, screenshot_each_step: f.shots,
    full_page_screenshots: f.fullpage, live_view: f.live, highlight: f.highlight !== false, self_heal: f.heal, use_llm: f.useLlm,
    timeout_ms: +f.timeout || 10000, dialogs: f.dialogs, slow_mo_ms: +f.slowmo || 0,
    profile_dir: f.profileDir.trim() || null, profile_name: f.profileName.trim() || null,
    session: f.session || null, save_session: f.saveSession.trim() || null,
  };
}

export const EXAMPLES = {
  "Shop (natural English)": "Go to ${base_url}/index.html, search for a ${product} and open the first ${brand} result. Add ${qty} of them to the cart, then go to the cart and make sure it shows ${brand}.",
  "Login (natural English)": "Open https://example.com/login and log in as ${user.email} with ${user.password}. Make sure the page says Welcome, then sign out.",
  "Shop: step by step": 'Open ${base_url}/index.html\nClick Accept cookies if shown\nType "${product}" into the search box\nPress Enter\nVerify page contains "results for"\nClick the first result containing "${brand}"\nSelect "${qty}" from Quantity\nClick Add to Cart button\nVerify page contains "Added to Cart"\nClick Go to Cart\nVerify page contains "Subtotal (${qty} items)"',
  "Login with a secret": 'Open https://example.com/login\nType "${user.email}" into Email\nType ${user.password} into Password\nClick Sign in button\nVerify page contains "Welcome"',
  "Capture SSO session": "Open https://intranet.example.com\nPause 90 seconds for SSO login\nVerify page contains \"Dashboard\"",
};

/** Fill ${key} / {{key}} placeholders for display, using the current test data (first iteration), secrets masked. */
export function previewValues(f) {
  let d;
  try { d = dataPayload(f); } catch { return () => null; }
  const data = d.iterations ? { ...(d.data || {}), ...d.iterations[0].data } : d.data || {};
  const secret = new Set(d.sensitive);
  const look = (path) => { let c = data; for (const k of path.split(".")) { if (c == null || typeof c !== "object") return undefined; c = c[k]; } return c; };
  return (text) => {
    if (!text || !/\$\{|\{\{/.test(text)) return null;
    return text.replace(/\$\{\s*([\w.-]+)\s*\}|\{\{\s*([\w.-]+)\s*\}\}/g, (m, a, b) => {
      const k = a || b, v = look(k);
      return v === undefined ? `⟨no value for ${k}⟩` : secret.has(k) ? "••••••" : String(v);
    });
  };
}
