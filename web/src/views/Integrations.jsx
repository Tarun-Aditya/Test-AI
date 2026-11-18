import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";

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
      <div className="panel-h"><h3>{c.label}</h3><span className={"slots " + (BADGE[c.status] || "")}>{c.status}</span>
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

export default function Integrations() {
  const { toast, project } = useApp();
  const [catalog, setCatalog] = useState(null);
  const [saved, setSaved] = useState({});
  const load = async () => {
    const [cat, conns] = await Promise.all([api("/api/integrations/catalog"), api("/api/connections")]);
    setCatalog(cat); setSaved(Object.fromEntries(conns.map((c) => [c.kind, c])));
  };
  useEffect(() => { load().catch((e) => toast(e.message, true)); }, [project]);
  if (!catalog) return <div className="empty-state">Loading…</div>;
  const groups = [["Standard", (c) => c.status === "standard"], ["Under development", (c) => c.status === "under development"], ["Planned", (c) => c.status === "planned"]];
  return (
    <>
      <p className="hint">Connections belong to the current project. Every tool is connected with a <b>token</b> (API token or personal access token) — no OAuth app
        to register. Tokens are encrypted on the hub and never sent back to the browser. Give each token the least access it needs: read for GitHub and Confluence;
        read + comment + transition (+ create issues for bugs) for Jira; write for the test tool.</p>
      {groups.map(([title, pick]) => (
        <div key={title}>
          <h3 style={{ margin: "18px 0 8px" }}>{title}</h3>
          <div className="grid2" style={{ alignItems: "start" }}>
            {catalog.filter(pick).map((c) => <Connection key={c.kind + (saved[c.kind]?.updated || "")} c={c} saved={saved[c.kind]} onSaved={load} />)}
          </div>
        </div>
      ))}
    </>
  );
}
