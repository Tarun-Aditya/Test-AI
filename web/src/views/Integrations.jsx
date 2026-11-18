import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

const MASKED = "••••••••";
const ROLE = { tracker: "stories & bugs", docs: "documents", code: "code & pull requests", tests: "test cases & results" };
const BADGE = { standard: "ok", "under development": "busy", planned: "" };

function Connection({ c, saved, onSaved }) {
  const { toast } = useApp();
  const [settings, setSettings] = useState(saved?.settings || {});
  const [secrets, setSecrets] = useState(saved?.secrets || {});
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k, v, secret) => (secret ? setSecrets : setSettings)((o) => ({ ...o, [k]: v }));
  const body = () => ({ settings, secrets: Object.fromEntries(Object.entries(secrets).filter(([, v]) => v && v !== MASKED)) });
  const save = async () => {
    setBusy(true);
    try { const r = await api(`/api/connections/${c.kind}`, { method: "PUT", body: body() }); setSecrets(r.secrets); onSaved(); toast(`${c.label} saved`); }
    catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const test = async () => {
    setBusy(true); setResult(null);
    try { setResult(await api(`/api/connections/${c.kind}/test`, { method: "POST", body: body() })); }
    catch (e) { setResult({ ok: false, message: e.message }); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!confirm(`Disconnect ${c.label}? Its token is deleted from the hub.`)) return;
    await api(`/api/connections/${c.kind}`, { method: "DELETE" }); setSettings({}); setSecrets({}); setResult(null); onSaved();
  };
  return (
    <div className="panel">
      <div className="panel-h"><h3>{c.label}</h3>{c.status !== "standard" && <span className="status queued">{c.status === "under development" ? "Beta" : c.status}</span>}
        <span className="spacer" />{saved && <span className="pill">connected</span>}</div>
      <div className="panel-b">
        <p className="hint" style={{ marginTop: 0 }}>{c.roles.map((r) => ROLE[r]).join(" · ")}{c.help ? <> — {c.help}</> : null}</p>
        {!c.available && <div className="mut">Planned. Tell us if you need it first.</div>}
        {c.available && <>
          {c.fields.map((f) => (
            <label className="field" key={f.key}><span>{f.label}</span>
              {f.options ? <select value={settings[f.key] || ""} onChange={(e) => set(f.key, e.target.value)}>
                <option value="">(automatic)</option>{f.options.map((o) => <option key={o}>{o}</option>)}</select>
                : <input type={f.secret ? "password" : "text"} autoComplete="off" placeholder={f.secret && secrets[f.key] === MASKED ? "saved: leave as is to keep it" : f.placeholder || ""}
                    value={f.secret ? (secrets[f.key] === MASKED ? "" : secrets[f.key] || "") : settings[f.key] || ""}
                    onChange={(e) => set(f.key, e.target.value, f.secret)} />}
            </label>))}
          <div className="row">
            <button className="btn primary sm" disabled={busy} onClick={save}>Save</button>
            <button className="btn sm" disabled={busy} onClick={test}>Test connection</button>
            <span className="spacer" />{saved && <button className="btn sm ghost danger" onClick={remove}>Disconnect</button>}
          </div>
          {result && <div className={"hint " + (result.ok ? "ok" : "err")} style={{ color: result.ok ? "var(--ok)" : "var(--bad)" }}>{result.ok ? "✓ " : "✗ "}{result.message}</div>}
        </>}
      </div>
    </div>
  );
}

const GROUPS = [["tracker", "Stories & bugs"], ["docs", "Documents"], ["code", "Code"], ["tests", "Test management"]];

export default function Integrations() {
  const { toast, project } = useApp();
  const [catalog, setCatalog] = useState(null);
  const [saved, setSaved] = useState({});
  const [pick, setPick] = useState(null);
  const load = async () => {
    const [cat, conns] = await Promise.all([api("/api/integrations/catalog"), api("/api/connections")]);
    setCatalog(cat); setSaved(Object.fromEntries(conns.map((c) => [c.kind, c])));
    setPick((p) => p || (conns[0]?.kind) || "jira");
  };
  useEffect(() => { setPick(null); load().catch((e) => toast(e.message, true)); }, [project]);
  if (!catalog) return <div className="intg"><div className="panel" style={{ padding: 16 }}><div className="skel" style={{ height: 16 }} /><div className="skel" style={{ height: 16, marginTop: 12, width: "60%" }} /></div></div>;
  const avail = catalog.filter((c) => c.status !== "planned");
  const planned = catalog.filter((c) => c.status === "planned");
  const cur = catalog.find((c) => c.kind === pick) || avail[0];
  const seen = new Set();
  return (
    <>
      <p className="hint" style={{ marginTop: 0 }}>Connections belong to the current project. Every tool connects with a <b>token</b> (API token or personal access token), no OAuth app.
        Tokens are encrypted on the hub and never sent back to the browser.</p>
      <div className="intg">
        <section className="panel" aria-label="Tools">
          {GROUPS.map(([role, label]) => {
            const items = avail.filter((c) => c.roles.includes(role) && !seen.has(c.kind));
            items.forEach((c) => seen.add(c.kind));
            return items.length ? <div key={role}>
              <div className="navh" style={{ padding: "12px 16px 4px" }}>{label}</div>
              {items.map((c) => (
                <button key={c.kind} className={"toolrow" + (cur?.kind === c.kind ? " on" : "")} aria-current={cur?.kind === c.kind ? "true" : undefined} onClick={() => setPick(c.kind)}>
                  <span className="tlogo" aria-hidden="true">{c.label.slice(0, 1)}</span>
                  <span style={{ flex: 1, minWidth: 0 }}><b>{c.label}</b><small>{c.roles.map((r) => ROLE[r]).join(" · ")}</small></span>
                  {saved[c.kind] ? <span className="status passed"><Icon name="ok" size={13} />Connected</span>
                    : c.status === "under development" ? <span className="status queued">Beta</span> : <span className="mut">Not set up</span>}
                </button>))}
            </div> : null;
          })}
          {planned.length > 0 && <div className="planned"><div className="navh" style={{ padding: "0 0 6px" }}>Planned</div>
            <div className="chips">{planned.map((c) => <span key={c.kind} className="pill">{c.label}</span>)}</div></div>}
        </section>
        {cur && <Connection key={cur.kind + (saved[cur.kind]?.updated || "")} c={cur} saved={saved[cur.kind]} onSaved={load} />}
      </div>
    </>
  );
}
