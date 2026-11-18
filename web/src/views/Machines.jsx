import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";


const PRESETS = {
  "Selenium Grid (your own)": { engine: "selenium", url: "http://grid-host:4444/wd/hub", browser: "chrome", platform: "Windows · Chrome", caps: { platformName: "windows" } },
  "BrowserStack · Windows 11 Chrome": { engine: "selenium", url: "https://USERNAME:ACCESS_KEY@hub-cloud.browserstack.com/wd/hub", browser: "chrome", platform: "Windows 11 · Chrome",
    caps: { browserVersion: "latest", "bstack:options": { os: "Windows", osVersion: "11" } } },
  "BrowserStack · macOS Safari": { engine: "selenium", url: "https://USERNAME:ACCESS_KEY@hub-cloud.browserstack.com/wd/hub", browser: "safari", platform: "macOS · Safari",
    caps: { browserVersion: "latest", "bstack:options": { os: "OS X", osVersion: "Sequoia" } } },
  "Sauce Labs · Windows 11 Edge": { engine: "selenium", url: "https://USERNAME:ACCESS_KEY@ondemand.us-west-1.saucelabs.com/wd/hub", browser: "msedge", platform: "Windows 11 · Edge",
    caps: { platformName: "Windows 11", browserVersion: "latest", "sauce:options": {} } },
  "LambdaTest · macOS Chrome": { engine: "selenium", url: "https://USERNAME:ACCESS_KEY@hub.lambdatest.com/wd/hub", browser: "chrome", platform: "macOS · Chrome",
    caps: { platformName: "macOS Sonoma", browserVersion: "latest", "LT:Options": {} } },
  "Playwright browser server": { engine: "playwright", url: "ws://host:3000/", browser: "chromium", platform: "remote Playwright", caps: {} },
};

function AgentTokens() {
  const { toast } = useApp();
  const [list, setList] = useState([]);
  const [fresh, setFresh] = useState(null);
  const load = () => api("/api/agent-tokens").then(setList).catch(() => {});
  useEffect(() => { load(); }, []);
  const add = async () => {
    const label = window.prompt("Which machine is this token for? (e.g. qa-docker-1, Ann's laptop)");
    if (!label) return;
    try { setFresh(await api("/api/agent-tokens", { method: "POST", body: { label } })); load(); } catch (e) { toast(e.message, true); }
  };
  const revoke = async (t) => { if (!window.confirm(`Revoke "${t.label}"? Machines using it are disconnected.`)) return; await api(`/api/agent-tokens/${t.id}`, { method: "DELETE" }); load(); toast("Revoked"); };
  return (
    <div className="panel" style={{ marginTop: 18 }}>
      <div className="panel-h"><h3>Agent tokens</h3><span className="spacer" /><button className="btn sm" onClick={add}>+ New token</button></div>
      <div className="panel-b">
        <p className="hint" style={{ marginTop: 0 }}>Each machine connects with its own token, so you can see which one is used where and revoke one without touching the others.</p>
        {fresh && <div className="card" style={{ marginBottom: 12 }}><h4>Token for "{fresh.label}" (copy it now, it is not shown again)</h4>
          <div className="copy"><span>{fresh.token}</span><button className="btn sm" onClick={() => navigator.clipboard?.writeText(fresh.token).then(() => toast("Copied"))}><Icon name="copy" size={14} />Copy</button></div></div>}
        {list.length > 0 && <table className="dt"><thead><tr><th>Machine</th><th>Created</th><th>Last used</th><th /></tr></thead><tbody>
          {list.map((t) => <tr key={t.id} className={t.revoked ? "off" : ""}><td><b>{t.label}</b>{t.created_by ? <div className="mut">by {t.created_by}</div> : null}</td>
            <td className="mut">{new Date(t.created * 1000).toLocaleString()}</td>
            <td className="mut">{t.last_used ? `${new Date(t.last_used * 1000).toLocaleString()}${t.last_machine ? ` · ${t.last_machine}` : ""}` : "never"}</td>
            <td>{t.revoked ? <span className="pill">revoked</span> : <button className="btn sm ghost danger" onClick={() => revoke(t)}>Revoke</button>}</td></tr>)}
        </tbody></table>}
      </div>
    </div>
  );
}

/** Add or edit one grid (Selenium Grid, cloud grid, Playwright browser server). */
function GridEditor({ edit, setEdit, onSaved }) {
  const { toast } = useApp();
  const save = async () => {
    try {
      let caps = {};
      try { caps = edit.caps.trim() ? JSON.parse(edit.caps) : {}; } catch (e) { throw new Error("Capabilities are not valid JSON: " + e.message); }
      const body = { name: edit.name, engine: edit.engine, browser: edit.browser || null, platform: edit.platform || null, slots: +edit.slots || 1, caps,
        url: edit.urlEdited || edit.isNew ? edit.url : null };
      await api(`/api/grids/${encodeURIComponent(edit.name)}`, { method: "PUT", body });
      setEdit(null); onSaved(); toast(`Grid "${edit.name}" saved. It is now in the machine picker`);
    } catch (e) { toast(e.message, true); }
  };
  const f = (k, v) => setEdit((e) => ({ ...e, [k]: v, ...(k === "url" ? { urlEdited: true } : {}) }));
  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-h"><h3>{edit.isNew ? "Add a grid" : `Edit ${edit.name}`}</h3><span className="spacer" /><button className="btn ghost sm" onClick={() => setEdit(null)}>Cancel</button></div>
      <div className="panel-b">
        <div className="grid2">
          <div className="field"><span>Name</span><input value={edit.name} disabled={!edit.isNew} onChange={(e) => f("name", e.target.value)} /></div>
          <div className="field"><span>Engine</span><div className="seg">{["selenium", "playwright"].map((x) =>
            <button key={x} className={edit.engine === x ? "on" : ""} onClick={() => f("engine", x)}>{x === "selenium" ? "Selenium" : "Playwright"}</button>)}</div></div>
        </div>
        <div className="field"><span>URL {!edit.isNew && <small>(leave as is to keep the stored URL and key)</small>}</span>
          <input className="mono" value={edit.url} onChange={(e) => f("url", e.target.value)} spellCheck={false} />
          <small>Selenium: the hub URL (credentials may go in it, they are stored on the hub only). Playwright: ws://… browser server or http://… DevTools endpoint.</small></div>
        <div className="grid2">
          <div className="field"><span>Browser</span><input value={edit.browser || ""} onChange={(e) => f("browser", e.target.value)} placeholder="chrome / msedge / firefox / safari" /></div>
          <div className="field"><span>Label (platform)</span><input value={edit.platform || ""} onChange={(e) => f("platform", e.target.value)} placeholder="Windows 11 · Chrome" /></div>
        </div>
        <div className="grid2">
          <div className="field"><span>Capabilities (JSON, Selenium)</span><textarea className="mono" rows={6} value={edit.caps} onChange={(e) => f("caps", e.target.value)} /></div>
          <div className="field"><span>Slots (parallel sessions)</span><input type="number" min={1} max={100} value={edit.slots} onChange={(e) => f("slots", e.target.value)} />
            <small>How many sessions to open at once: your plan's concurrency.</small></div>
        </div>
        <button className="btn primary" onClick={save}>Save grid</button>
      </div>
    </div>
  );
}

export default function Machines() {
  const { machines, health, form, setForm, nav, toast, loadMachines, can } = useApp();
  const [grids, setGrids] = useState([]);
  const [edit, setEdit] = useState(null);
  const [howto, setHowto] = useState(false);
  const loadGrids = () => api("/api/grids").then(setGrids).catch(() => {});
  useEffect(() => { loadGrids(); }, []);
  const agents = machines.filter((m) => m.kind !== "grid");
  useEffect(() => { if (!agents.length) setHowto(true); }, [agents.length]);
  const cmd = `python -m aiauto agent --server ${location.origin} --name my-laptop --token <token> --slots 2`;
  const dcmd = `docker run -d --shm-size=2g autopilot agent --server ${location.origin} --name docker-1 --token <token> --slots 2`;
  const use = (name) => { setForm({ agent: name, agents: [name] }); nav("studio"); toast(`Tests will run on ${name === "local" ? health.local_name : name}`); };
  const picked = (n) => (form.agents || [form.agent]).includes(n);
  const forget = async (n) => { try { await api(`/api/agents/${encodeURIComponent(n)}`, { method: "DELETE" }); loadMachines(); } catch (e) { toast(e.message, true); } };
  const removeGrid = async (n) => { if (!confirm(`Remove the grid "${n}"?`)) return; await api(`/api/grids/${encodeURIComponent(n)}`, { method: "DELETE" }); loadGrids(); loadMachines(); };
  const addGrid = (preset) => {
    const p = PRESETS[preset];
    setEdit({ name: preset.split("·").pop().trim().toLowerCase().replace(/[^a-z0-9]+/g, "-"), slots: 2, ...p, caps: JSON.stringify(p.caps, null, 2), isNew: true });
  };
  const rows = [
    { key: "local", name: health.local_name, kind: "This server", online: true, busy: health.local_running > 0, what: "playwright, selenium",
      use: `${health.local_running || 0} of ${health.local_slots || 1} slots busy` },
    ...agents.map((m) => ({ key: m.name, name: m.name, kind: m.platform || "Machine", online: m.online, busy: m.busy, what: [...(m.engines || []), ...(m.browsers || [])].join(", "),
      use: m.online ? `${m.running || 0} of ${m.slots || 1} slots busy${m.queued ? ` · ${m.queued} waiting` : ""}` : `offline · last seen ${new Date(m.last_seen * 1000).toLocaleString()}`, agent: m })),
    ...grids.map((g) => ({ key: "g:" + g.name, name: g.name, kind: `Grid · ${g.engine}`, online: true, what: `${g.platform || "—"} · ${g.browser || "default browser"}`, use: `${g.slots} slots`, grid: g })),
  ];
  return (
    <section className="view on">
      <div className="panel">
        <div className="panel-h"><h3>Machines &amp; grids</h3><span className="mut">{rows.filter((r) => r.online).length} available</span><span className="spacer" />
          {can.admin && <label><span className="sr">Add a grid</span><select className="in" style={{ width: "auto" }} value="" onChange={(e) => e.target.value && addGrid(e.target.value)}>
            <option value="">Add a grid…</option>{Object.keys(PRESETS).map((k) => <option key={k}>{k}</option>)}</select></label>}
          <button className="btn sm" aria-expanded={howto} onClick={() => setHowto(!howto)}><Icon name="plus" size={14} />Add a machine</button></div>
        <div style={{ overflowX: "auto" }}><table className="table compact">
          <thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Runs</th><th>Capacity</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.key}>
              <td><b>{r.name}</b>{picked(r.grid ? r.name : r.key) && <span className="pill" style={{ marginLeft: 8 }}>selected</span>}</td>
              <td className="mut nowrap">{r.kind}</td>
              <td>{r.online ? <span className={"status " + (r.busy ? "running" : "passed")}>{r.busy ? "busy" : "online"}</span> : <span className="status cancelled">offline</span>}</td>
              <td className="mut">{r.what}</td><td className="mut nowrap">{r.use}</td>
              <td><div className="rowacts">
                {r.online && <button className="btn sm" onClick={() => use(r.grid ? r.name : r.key)}>Run tests here</button>}
                {r.grid && can.admin && <><button className="btn sm" onClick={() => setEdit({ ...r.grid, caps: JSON.stringify(r.grid.caps || {}, null, 2) })}>Edit</button>
                  <button className="btn sm ghost danger" onClick={() => removeGrid(r.name)}>Remove</button></>}
                {r.agent && !r.online && <button className="btn sm ghost danger" onClick={() => forget(r.name)}>Forget</button>}
              </div></td>
            </tr>))}</tbody>
        </table></div>
      </div>
      {edit && <GridEditor edit={edit} setEdit={setEdit} onSaved={() => { loadGrids(); loadMachines(); }} />}
      {howto && <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-h"><h3>Add a machine</h3><span className="spacer" /><button className="btn ghost sm" onClick={() => setHowto(false)}>Hide</button></div>
        <div className="panel-b">
          <p className="hint" style={{ marginTop: 0 }}>Install this project on any computer with browsers (laptop, VM, CI runner) and connect it. It opens an outbound
            connection, so it works behind firewalls, and it appears in the machine picker. <code>--slots N</code> runs N tests on it at the same time.</p>
          {[["On a computer", cmd], ["In Docker (Playwright, Selenium and browsers included)", dcmd]].map(([l, c]) => (
            <div key={l} style={{ marginBottom: 10 }}><small className="mut">{l}</small>
              <div className="copy"><span>{c}</span><button className="btn sm" onClick={() => navigator.clipboard?.writeText(c).then(() => toast("Copied"))}><Icon name="copy" size={14} />Copy</button></div></div>))}
          <p className="hint" style={{ marginBottom: 0 }}>A <b>grid</b> runs the browser somewhere else instead: your own Selenium Grid with Windows or Mac nodes, BrowserStack, Sauce Labs,
            LambdaTest or a Playwright browser server. Reports, video and scripts are the same.</p>
        </div>
      </div>}
      {can.admin && <AgentTokens />}
    </section>
  );
}
