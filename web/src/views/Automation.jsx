import { useCallback, useEffect, useState } from "react";
import { ago, api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

const TABS = [["schedules", "Schedules"], ["hooks", "CI hooks"], ["alerts", "Alerts"], ["flaky", "Flaky tests"]];

function Field({ label, hint, children }) {
  return <div className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</div>;
}
const dataOf = (text) => {
  const out = {};
  (text || "").split("\n").forEach((l) => { const i = l.indexOf("="); if (i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1).trim(); });
  return Object.keys(out).length ? out : null;
};
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "–");
const PRESETS = [
  ["*/15 * * * *", "Every 15 minutes"], ["0 * * * *", "Every hour"], ["30 2 * * *", "Every night at 02:30"],
  ["0 9 * * mon-fri", "Weekdays at 09:00"], ["0 6 * * mon", "Mondays at 06:00"], ["", "Custom…"],
];

function useList(path) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const load = useCallback(() => api(path).then((r) => { setRows(r); setErr(null); }).catch((e) => { setRows([]); setErr(e.message); }), [path]);
  useEffect(() => { load(); }, [load]);
  return [rows, load, err];
}

/** The settings both schedules and hooks need: which script, on which engine and machine, with what data. */
function RunSettings({ v, set }) {
  const { library, machines, health } = useApp();
  const online = machines.filter((m) => m.online);
  return <>
    <div className="grid2">
      <Field label="Script" hint="A script from the Library. Save a passing run there first">
        <select value={v.library} onChange={(e) => set({ library: e.target.value })} aria-label="Script">
          <option value="">Choose a script…</option>{library.map((l) => <option key={l.name} value={l.name}>{l.name} ({l.steps} steps)</option>)}</select></Field>
      <Field label="Engine">
        <select value={v.engine} onChange={(e) => set({ engine: e.target.value })} aria-label="Engine">
          <option value="playwright">Playwright</option><option value="selenium">Selenium</option><option value="both">Both</option></select></Field>
      <Field label="Machine" hint="Where it runs">
        <select value={v.agent} onChange={(e) => set({ agent: e.target.value })} aria-label="Machine">
          <option value="local">{health.local_name || "This hub"}</option><option value="any">Any free machine</option>
          {online.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}</select></Field>
      <Field label="Browser window">
        <select value={v.headless ? "h" : "v"} onChange={(e) => set({ headless: e.target.value === "h" })} aria-label="Browser window">
          <option value="h">Hidden (headless)</option><option value="v">Visible</option></select></Field>
    </div>
    <Field label="Data (optional)" hint="One per line: name = value. Secrets stay on the machine that runs the script (AIAUTO_SECRET_<NAME>)">
      <textarea rows={3} value={v.data} placeholder={"base_url = https://staging.example.com"} onChange={(e) => set({ data: e.target.value })} aria-label="Data" /></Field>
  </>;
}
const configOf = (v) => ({ config: { headless: v.headless, record_video: true, use_llm: false, engine: v.engine === "both" ? "playwright" : v.engine },
  engines: v.engine === "both" ? ["playwright", "selenium"] : null, agent: v.agent, data: dataOf(v.data) });
const BLANK = { library: "", engine: "playwright", agent: "local", headless: true, data: "" };

// ------------------------------------------------------------------------------------------------ schedules
function Schedules({ preset, can }) {
  const { toast, openRun } = useApp();
  const [rows, load, err] = useList("/api/schedules");
  const [form, setForm] = useState(preset ? { ...BLANK, library: preset } : null);
  const open = form !== null;
  const set = (p) => setForm((f) => ({ ...f, ...p }));
  const [next, setNext] = useState([]);
  const [bad, setBad] = useState(null);
  useEffect(() => {
    if (!form?.cron) { setNext([]); setBad(null); return; }
    const h = setTimeout(() => api(`/api/schedules/preview?cron=${encodeURIComponent(form.cron)}&count=3`).then((r) => { setNext(r.next); setBad(null); }).catch((e) => { setNext([]); setBad(e.message); }), 250);
    return () => clearTimeout(h);
  }, [form?.cron]);
  const save = async () => {
    try {
      await api("/api/schedules", { method: "PUT", body: { id: form.id, name: form.name || null, library: form.library, cron: form.cron, enabled: form.enabled !== false, ...configOf(form) } });
      setForm(null); load(); toast("Schedule saved");
    } catch (e) { toast(e.message, true); }
  };
  const act = async (fn, msg) => { try { await fn(); load(); msg && toast(msg); } catch (e) { toast(e.message, true); } };
  const edit = (s) => setForm({ ...BLANK, id: s.id, name: s.name || "", library: s.library, cron: s.cron, enabled: s.enabled !== false,
    engine: s.engines?.length > 1 ? "both" : s.config?.engine || "playwright", agent: s.agent || "local", headless: s.config?.headless !== false,
    data: Object.entries(s.data || {}).map(([k, v]) => `${k} = ${v}`).join("\n") });
  return (
    <>
      {err && <div className="callout bad">{err}</div>}
      <div className="cards">
        {(rows || []).map((s) => (
          <div className={"card" + (s.enabled === false ? " off" : "")} key={s.id}>
            <h4><Icon name="clock" style={{ color: "var(--acc)" }} />{s.name || s.library}</h4>
            <div className="mut">runs <b>{s.library}</b> · <code>{s.cron}</code></div>
            <div className="row">
              <span className="pill">{s.enabled === false ? "paused" : `next: ${when(s.next_run)}`}</span>
              {s.last_run && <span className="pill">last: {ago(s.last_run)}</span>}
              {s.last_error && <span className="pill" style={{ color: "var(--bad)" }} title={s.last_error}>could not start</span>}
            </div>
            {s.last_run_ids?.length > 0 && <div className="row">{s.last_run_ids.slice(0, 3).map((id) => <button key={id} className="btn sm ghost" onClick={() => openRun(id)}>Open last run</button>)}</div>}
            {can.run && <div className="row" style={{ marginTop: "auto" }}>
              <button className="btn sm primary" onClick={() => act(() => api(`/api/schedules/${s.id}/run`, { method: "POST" }), "Started")}><Icon name="play" size={12} />Run now</button>
              <button className="btn sm" onClick={() => edit(s)}>Edit</button>
              <button className="btn sm" onClick={() => act(() => api("/api/schedules", { method: "PUT", body: { id: s.id, library: s.library, cron: s.cron, name: s.name, enabled: s.enabled === false, config: s.config || {}, agent: s.agent || "local", engines: s.engines, data: s.data } }), s.enabled === false ? "Resumed" : "Paused")}>{s.enabled === false ? "Resume" : "Pause"}</button>
              <span className="spacer" /><button className="btn sm ghost danger" onClick={() => confirm(`Delete schedule “${s.name || s.library}”?`) && act(() => api(`/api/schedules/${s.id}`, { method: "DELETE" }), "Deleted")}>Delete</button>
            </div>}
          </div>
        ))}
        {rows && !rows.length && !open && <div className="card"><h4>No schedules yet</h4><div className="mut">Run a saved script every night, every hour, or on weekdays. The hub's clock decides.</div></div>}
      </div>
      {can.run && !open && <p><button className="btn primary" onClick={() => setForm({ ...BLANK, cron: "30 2 * * *" })}><Icon name="plus" size={14} />New schedule</button></p>}
      {open && (
        <div className="card" style={{ marginTop: 14, maxWidth: 760 }} role="form" aria-label="Schedule">
          <h4>{form.id ? "Edit schedule" : "New schedule"}</h4>
          <Field label="Name (optional)"><input value={form.name || ""} onChange={(e) => set({ name: e.target.value })} placeholder="Nightly checkout" aria-label="Name" /></Field>
          <RunSettings v={form} set={set} />
          <div className="grid2">
            <Field label="How often">
              <select value={PRESETS.some(([c]) => c === form.cron) ? form.cron : ""} onChange={(e) => set({ cron: e.target.value || form.cron })} aria-label="How often">
                {PRESETS.map(([c, l]) => <option key={l} value={c}>{l}</option>)}</select></Field>
            <Field label="Cron expression" hint="minute hour day-of-month month day-of-week, e.g. 30 2 * * *  ·  also @daily, @hourly, @weekly">
              <input value={form.cron || ""} onChange={(e) => set({ cron: e.target.value })} className="mono" aria-label="Cron expression" /></Field>
          </div>
          {bad ? <div className="callout bad" role="alert">{bad}</div> : next.length > 0 && <div className="callout">Next runs: {next.map(when).join("  ·  ")}</div>}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={!form.library || !form.cron || !!bad} onClick={save}>Save schedule</button>
            <button className="btn" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ CI hooks
function Hooks({ can }) {
  const { toast, openRun } = useApp();
  const [rows, load, err] = useList("/api/hooks");
  const [form, setForm] = useState(null);
  const [made, setMade] = useState(null);
  const set = (p) => setForm((f) => ({ ...f, ...p }));
  const create = async () => {
    try {
      const h = await api("/api/hooks", { method: "POST", body: { name: form.name || null, library: form.library, ...configOf(form) } });
      setMade({ ...h, full: `${location.origin}${h.url}` }); setForm(null); load();
    } catch (e) { toast(e.message, true); }
  };
  const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast("Copied"), () => toast("Select the text and copy it", true));
  return (
    <>
      {err && <div className="callout bad">{err}</div>}
      <p className="hint">A hook is a private URL your pipeline calls to run a saved script. No sign-in: the secret is in the URL, so keep it in your CI's secret store.</p>
      {made && (
        <div className="card" style={{ maxWidth: 820, borderColor: "var(--acc)" }} role="status">
          <h4>Hook created. Copy the URL now: it is shown once</h4>
          <div className="row"><code className="mono" style={{ wordBreak: "break-all", flex: 1 }}>{made.full}</code><button className="btn sm" onClick={() => copy(made.full)}><Icon name="copy" size={12} />Copy URL</button></div>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", background: "var(--sunken)", padding: 10, borderRadius: 8, margin: "10px 0 0", fontSize: 12 }}>{`curl -X POST "${made.full}"`}</pre>
          <div className="row" style={{ marginTop: 8 }}><button className="btn sm" onClick={() => copy(`curl -X POST "${made.full}"`)}>Copy curl</button><button className="btn sm ghost" onClick={() => setMade(null)}>I've saved it</button></div>
        </div>
      )}
      <div className="cards">
        {(rows || []).map((h) => (
          <div className="card" key={h.id}>
            <h4><Icon name="code" style={{ color: "var(--acc)" }} />{h.name || h.library}</h4>
            <div className="mut">runs <b>{h.library}</b></div>
            <div className="row">{h.last_run ? <span className="pill">called {ago(h.last_run)}</span> : <span className="pill">never called</span>}</div>
            {h.last_run_ids?.length > 0 && <div className="row"><button className="btn sm ghost" onClick={() => openRun(h.last_run_ids[0])}>Open last run</button></div>}
            {can.run && <div className="row" style={{ marginTop: "auto" }}><span className="spacer" />
              <button className="btn sm ghost danger" onClick={() => confirm("Delete this hook? Pipelines using it will stop working.") && api(`/api/hooks/${h.id}`, { method: "DELETE" }).then(() => { load(); toast("Deleted"); }).catch((e) => toast(e.message, true))}>Delete</button></div>}
          </div>
        ))}
        {rows && !rows.length && !form && !made && <div className="card"><h4>No hooks yet</h4><div className="mut">Create one, then call it from GitHub Actions, GitLab, Jenkins or Azure Pipelines after a deploy.</div></div>}
      </div>
      {can.run && !form && <p><button className="btn primary" onClick={() => setForm({ ...BLANK })}><Icon name="plus" size={14} />New hook</button></p>}
      {form && (
        <div className="card" style={{ marginTop: 14, maxWidth: 760 }} role="form" aria-label="CI hook">
          <h4>New hook</h4>
          <Field label="Name (optional)"><input value={form.name || ""} onChange={(e) => set({ name: e.target.value })} placeholder="After staging deploy" aria-label="Name" /></Field>
          <RunSettings v={form} set={set} />
          <div className="row"><button className="btn primary" disabled={!form.library} onClick={create}>Create hook</button><button className="btn" onClick={() => setForm(null)}>Cancel</button></div>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ alerts
const KINDS = { slack: "Slack", teams: "Microsoft Teams", webhook: "Webhook", email: "Email" };
const ONS = { failed: "when a run fails", always: "after every run", recovered: "when a failing test passes again" };

function Alerts({ can }) {
  const { toast, projects } = useApp();
  const [rows, load, err] = useList("/api/notifications");
  const [form, setForm] = useState(null);
  const set = (p) => setForm((f) => ({ ...f, ...p }));
  const save = async () => {
    try {
      await api("/api/notifications", { method: "PUT", body: { kind: form.kind, url: form.kind === "email" ? null : form.url, to: form.kind === "email" ? form.to : null, on: form.on, project: form.project || null } });
      setForm(null); load(); toast("Channel saved");
    } catch (e) { toast(e.message, true); }
  };
  const test = (r) => api(`/api/notifications/${r.id}/test`, { method: "POST" }).then(() => toast("Test message sent"), (e) => toast(e.message, true));
  const target = (r) => (r.kind === "email" ? r.to : (() => { try { return new URL(r.url).host; } catch { return r.url; } })());
  return (
    <>
      {err && <div className="callout bad">{err}</div>}
      {!can.admin && <p className="hint">Only an admin can add or change alert channels.</p>}
      <div className="cards">
        {(rows || []).map((r) => (
          <div className="card" key={r.id}>
            <h4><Icon name="inbox" style={{ color: "var(--acc)" }} />{KINDS[r.kind] || r.kind}</h4>
            <div className="mut">{target(r)}</div>
            <div className="row"><span className="pill">{ONS[r.on] || r.on}</span>{r.project && <span className="pill">project: {r.project}</span>}</div>
            {can.admin && <div className="row" style={{ marginTop: "auto" }}>
              <button className="btn sm" onClick={() => test(r)}>Send test</button><span className="spacer" />
              <button className="btn sm ghost danger" onClick={() => confirm("Remove this channel?") && api(`/api/notifications/${r.id}`, { method: "DELETE" }).then(() => { load(); toast("Removed"); })}>Remove</button></div>}
          </div>
        ))}
        {rows && !rows.length && !form && <div className="card"><h4>No alert channels</h4><div className="mut">Get a Slack or Teams message, a webhook call or an email when a run fails.</div></div>}
      </div>
      {can.admin && !form && <p><button className="btn primary" onClick={() => setForm({ kind: "slack", url: "", to: "", on: "failed", project: "" })}><Icon name="plus" size={14} />Add a channel</button></p>}
      {form && (
        <div className="card" style={{ marginTop: 14, maxWidth: 760 }} role="form" aria-label="Alert channel">
          <h4>New alert channel</h4>
          <div className="grid2">
            <Field label="Where"><select value={form.kind} onChange={(e) => set({ kind: e.target.value })} aria-label="Where">{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Tell me"><select value={form.on} onChange={(e) => set({ on: e.target.value })} aria-label="Tell me">{Object.entries(ONS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          </div>
          {form.kind === "email"
            ? <Field label="Send to" hint="The hub needs an SMTP server: AIAUTO_SMTP_HOST, _PORT, _USER, _PASSWORD, _FROM"><input type="email" value={form.to} onChange={(e) => set({ to: e.target.value })} placeholder="team@example.com" aria-label="Send to" /></Field>
            : <Field label="Webhook URL" hint={form.kind === "slack" ? "Slack: Apps → Incoming Webhooks" : form.kind === "teams" ? "Teams: channel → Connectors → Incoming Webhook" : "Receives a JSON document about the run"}>
              <input value={form.url} onChange={(e) => set({ url: e.target.value })} placeholder="https://hooks.slack.com/services/…" aria-label="Webhook URL" /></Field>}
          {projects.length > 1 && <Field label="Only for project"><select value={form.project} onChange={(e) => set({ project: e.target.value })} aria-label="Only for project"><option value="">All projects</option>{projects.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}</select></Field>}
          <div className="callout">A message links back to the run when the hub knows its address: set <code>AIAUTO_PUBLIC_URL</code>.</div>
          <div className="row" style={{ marginTop: 12 }}><button className="btn primary" disabled={form.kind === "email" ? !form.to : !form.url} onClick={save}>Save channel</button><button className="btn" onClick={() => setForm(null)}>Cancel</button></div>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ flaky tests
export function Dots({ history }) {
  return <span className="dots" role="img" aria-label={`Recent results, oldest first: ${history.join(", ")}`}>
    {history.map((h, i) => <i key={i} className={h === "passed" ? "p" : "f"} title={h} />)}</span>;
}

function Flaky() {
  const { openRun } = useApp();
  const [only, setOnly] = useState(false);
  const [rows, , err] = useList(`/api/flaky?only=${only}`);
  return (
    <>
      {err && <div className="callout bad">{err}</div>}
      <p className="hint">A test is <b>flaky</b> when it both passes and fails across its recent runs and the result keeps flipping. A test that broke once and stayed broken is failing, not flaky.</p>
      <label className="toggle" style={{ marginBottom: 12 }}><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /><span className="sw" />Only flaky tests</label>
      {rows && !rows.length ? <div className="empty-state">{only ? "No flaky tests. Nice." : "No finished runs yet."}</div> :
        <div style={{ overflowX: "auto" }}><table className="table compact"><thead><tr><th>Test</th><th>Recent results</th><th>Pass rate</th><th>Flips</th><th>Healed</th><th /></tr></thead><tbody>
          {(rows || []).map((r) => (
            <tr key={r.name} className="clickable" onClick={() => r.last_run && openRun(r.last_run)}>
              <td><b>{r.name}</b> {r.flaky && <span className="status failed" style={{ marginLeft: 6 }}>flaky</span>}</td>
              <td><Dots history={r.history} /></td>
              <td className="mono">{Math.round((r.pass_rate ?? 0) * 100)}% <span className="mut">({r.runs} runs)</span></td>
              <td className="mono">{r.flips}</td>
              <td className="mono">{r.healed_runs || 0}</td>
              <td><button className="btn sm ghost" onClick={(e) => { e.stopPropagation(); r.last_run && openRun(r.last_run); }}>Latest run</button></td>
            </tr>))}
        </tbody></table></div>}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ page
export default function Automation() {
  const { route, nav, can } = useApp();
  const tab = TABS.some(([k]) => k === route.id) ? route.id : "schedules";
  return (
    <section className="view on">
      <div className="tabs" role="tablist" aria-label="Automation" style={{ marginBottom: 16 }}>
        {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => nav("automation", k)}>{l}</button>)}
      </div>
      {tab === "schedules" && <Schedules key={route.tab || ""} preset={route.tab} can={can} />}
      {tab === "hooks" && <Hooks can={can} />}
      {tab === "alerts" && <Alerts can={can} />}
      {tab === "flaky" && <Flaky />}
    </section>
  );
}
