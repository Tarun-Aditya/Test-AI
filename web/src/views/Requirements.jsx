import { useEffect, useState } from "react";
import { ago, api, store } from "../api.js";
import { useApp } from "../App.jsx";
import { runConfig } from "../form.js";
import { agentsOf } from "../components/MachinePicker.jsx";
import Icon from "../icons.jsx";

const TYPE_COLOR = { positive: "var(--ok)", negative: "var(--bad)", boundary: "var(--warn)", regression: "var(--acc)", role: "var(--mut)" };
const stCls = (s) => (s === "passed" ? "ok" : s === "failed" ? "bad" : "busy");

function Pack({ pack }) {
  const i = pack.issue, code = pack.code || {};
  return (
    <div className="panel-b">
      <div className="mut"><a href={i.url} target="_blank" rel="noreferrer">{i.key}</a> · {i.type} · {i.status}{i.labels?.length ? " · " + i.labels.join(", ") : ""}</div>
      {i.acceptance?.length > 0 && <><b>Acceptance criteria</b><ol style={{ margin: "4px 0 10px 18px", padding: 0 }}>{i.acceptance.map((a, n) => <li key={n}>{a}</li>)}</ol></>}
      {i.steps_to_reproduce?.length > 0 && <><b>Steps to reproduce</b><ol style={{ margin: "4px 0 10px 18px", padding: 0 }}>{i.steps_to_reproduce.map((a, n) => <li key={n}>{a}</li>)}</ol>
        {i.expected_result && <div className="mut">Expected: {i.expected_result}</div>}</>}
      {!i.acceptance?.length && !i.steps_to_reproduce?.length && <div className="hint">No acceptance criteria found. Add an “Acceptance criteria” section (or field) to the story for better cases.</div>}
      <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
        {(pack.docs || []).map((d) => <a key={d.url} className="pill" href={d.url} target="_blank" rel="noreferrer"><Icon name="doc" size={12} /> {d.title}</a>)}
        {(code.prs || []).map((p) => <a key={p.url} className="pill" href={p.url} target="_blank" rel="noreferrer">PR #{p.number} {p.repo}</a>)}
      </div>
      {code.fields?.length > 0 && <div className="mut" style={{ marginTop: 6 }}>Form in the code: {code.fields.map((f) =>
        `${f.name}${f.required ? "*" : ""}${f.maxlength ? ` ≤${f.maxlength}` : ""}${f.minlength ? ` ≥${f.minlength}` : ""}${f.min || f.max ? ` [${f.min ?? ""}–${f.max ?? ""}]` : ""}`).join(", ")}
        {code.buttons?.length ? ` · buttons: ${code.buttons.join(", ")}` : ""}</div>}
      {pack.warnings?.length > 0 && <div className="hint" style={{ color: "var(--warn)" }}>{pack.warnings.join(" · ")}</div>}
    </div>
  );
}

function CaseRow({ c, latest, selected, onSelect, onChange, openRun }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState(c.steps.join("\n"));
  const [title, setTitle] = useState(c.title);
  useEffect(() => { setSteps(c.steps.join("\n")); setTitle(c.title); }, [c.steps.join("\n"), c.title]);
  const dirty = steps !== c.steps.join("\n") || title !== c.title;
  return (
    <>
      <tr className={c.status === "rejected" ? "mut" : ""}>
        <td><input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} /></td>
        <td><span className="pill" style={{ color: TYPE_COLOR[c.type] }}>{c.type}</span></td>
        <td><button className="linkish" onClick={() => setOpen(!open)} style={{ textAlign: "left" }}>{c.title.replace(/^[A-Z][A-Z0-9_]+-\d+ · /, "")}</button>
          {c.notes?.length > 0 && <div className="hint" style={{ color: "var(--warn)", margin: 0 }}>{c.notes.join(" · ")}</div>}</td>
        <td className="mut">{c.ac ? `AC ${c.ac}` : c.source === "steps to reproduce" ? "repro" : ""}</td>
        <td className="mut">{c.origin}</td>
        <td><span className={"slots " + (c.status === "approved" ? "ok" : c.status === "rejected" ? "" : "busy")}>{c.status}</span></td>
        <td>{latest ? <button className={"slots " + stCls(latest.status)} onClick={() => openRun(latest.run_id)}>{latest.status}</button> : <span className="mut">–</span>}</td>
        <td className="mut">{Object.entries(c.external || {}).map(([k, v]) => <div key={k}>{v}</div>)}</td>
      </tr>
      {open && <tr><td /><td colSpan={7}>
        <label className="field"><span>Title</span><input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label className="field"><span>Steps (one per line, the same English Studio understands)</span>
          <textarea rows={Math.min(14, steps.split("\n").length + 1)} value={steps} onChange={(e) => setSteps(e.target.value)} style={{ fontFamily: "var(--mono)" }} /></label>
        {c.expected && <div className="mut">Expected: {c.expected}</div>}
        {c.source && <div className="mut">From: {c.source}</div>}
        {Object.keys(c.data || {}).length > 0 && <div className="mut">Data: {Object.keys(c.data).map((k) => `${k}=${(c.sensitive || []).includes(k) ? "••••" : c.data[k]}`).join(", ")}</div>}
        <div className="row"><button className="btn sm primary" disabled={!dirty} onClick={() => onChange({ ...c, title, steps: steps.split("\n") })}>Save case</button></div>
      </td></tr>}
    </>
  );
}

function Design({ id, onClose }) {
  const { toast, form, openRun, subscribe } = useApp();
  const [d, setD] = useState(null);
  const [log, setLog] = useState([]);
  const [sel, setSel] = useState(new Set());
  const [baseUrl, setBaseUrl] = useState(() => store.get("designBaseUrl", ""));
  const [busy, setBusy] = useState(false);
  const load = async () => { const [x, l] = await Promise.all([api(`/api/designs/${id}`), api(`/api/designs/${id}/log`)]); setD(x); setLog(l); };
  useEffect(() => { load().catch((e) => toast(e.message, true)); }, [id]);
  useEffect(() => subscribe?.((ev) => { if (ev.type === "runs_changed") setTimeout(() => load().catch(() => {}), 1500); }), [id]);
  if (!d) return <div className="panel"><div className="panel-b">Loading…</div></div>;
  const act = async (fn, msg) => { setBusy(true); try { const r = await fn(); if (msg) toast(typeof msg === "function" ? msg(r) : msg); await load(); return r; } catch (e) { toast(e.message, true); } finally { setBusy(false); } };
  const ids = [...sel];
  const status = (s) => act(async () => { const r = await api(`/api/designs/${id}/status`, { method: "POST", body: { case_ids: ids, status: s } });
    if (r.blocked?.length) toast(`Fix these first: ${r.blocked.join(", ")}`, true); setSel(new Set()); return r; });
  const saveCase = (c) => act(() => api(`/api/designs/${id}`, { method: "PUT", body: { cases: d.cases.map((x) => (x.id === c.id ? c : x)) } }), "Case saved");
  const push = () => act(() => api(`/api/designs/${id}/push`, { method: "POST" }), (r) => `Pushed ${Object.values(r.pushed).filter(Boolean).length} case(s)`);
  const refresh = () => act(() => api(`/api/designs/${id}/refresh`, { method: "POST" }), (r) => (r.changed ? "The story or its code changed: review the cases" : "Nothing changed"));
  const del = () => act(async () => { if (!confirm("Delete this design (its sync log too)?")) return; await api(`/api/designs/${id}`, { method: "DELETE" }); onClose(); });
  const run = () => act(async () => {
    if (!baseUrl.trim()) throw new Error("Set the URL of the app under test");
    store.set("designBaseUrl", baseUrl.trim());
    const agents = agentsOf(form);
    const r = await api(`/api/designs/${id}/run`, { method: "POST", body: {
      base_url: baseUrl.trim(), case_ids: ids.length ? ids : null, config: runConfig(form),
      agent: agents.length > 1 ? agents : agents[0], engines: form.engine === "both" ? ["playwright", "selenium"] : null } });
    if (r.skipped?.length) toast(`Skipped (steps not understood): ${r.skipped.map((s) => s.case).join(", ")}`, true);
    return r;
  }, (r) => r && `Started ${r.run_ids.length} run(s). Results go back to the tracker and test tool when they finish`);
  const approved = d.cases.filter((c) => c.status === "approved").length;
  const allSel = sel.size === d.cases.length && d.cases.length > 0;
  return (
    <div className="panel">
      <div className="panel-h"><h3>Test cases · {d.issue_key}</h3><span className="mut">&nbsp;{d.cases.length} cases, {approved} approved · {d.status}</span>
        <span className="spacer" /><button className="btn sm ghost" onClick={onClose}><Icon name="x" size={12} /></button></div>
      <div className="panel-b">
        {d.notes?.length > 0 && <div className="hint" style={{ color: "var(--warn)" }}>{d.notes.join(" · ")}</div>}
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          <button className="btn sm" disabled={busy || !sel.size} onClick={() => status("approved")}>Approve{sel.size ? ` (${sel.size})` : ""}</button>
          <button className="btn sm" disabled={busy || !sel.size} onClick={() => status("rejected")}>Reject</button>
          <button className="btn sm" disabled={busy || !sel.size} onClick={() => status("draft")}>Back to draft</button>
          <button className="btn sm" disabled={busy || !approved} onClick={push} title="Create / update the approved cases in Zephyr Scale (or the connected test tool)">Push to test tool</button>
          <button className="btn sm ghost" disabled={busy} onClick={refresh} title="Re-read the story, docs and code">Re-check story</button>
          <span className="spacer" /><button className="btn sm ghost danger" disabled={busy} onClick={del}>Delete</button>
        </div>
        <div className="row" style={{ gap: 6, marginTop: 8 }}>
          <input style={{ flex: 1, minWidth: 0, width: "100%" }} placeholder="URL of the app under test (fills ${base_url}), e.g. https://staging.shop.example" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          <button className="btn primary sm" disabled={busy || !approved} onClick={run}><Icon name="play" size={12} />Run {ids.length ? "selected" : "approved"}</button>
        </div>
        <div className="hint" style={{ marginTop: 4 }}>Runs use the engine, machine and options from the top bar and Studio.</div>
        <table className="dt" style={{ marginTop: 10 }}><thead><tr>
          <th><input type="checkbox" checked={allSel} onChange={(e) => setSel(new Set(e.target.checked ? d.cases.map((c) => c.id) : []))} /></th>
          <th>Type</th><th>Case</th><th>Covers</th><th>Origin</th><th>Status</th><th>Last run</th><th>In tool</th></tr></thead>
          <tbody>{d.cases.map((c) => <CaseRow key={c.id} c={c} latest={d.latest?.[c.id]} selected={sel.has(c.id)} openRun={openRun} onChange={saveCase}
            onSelect={(on) => setSel((s) => { const n = new Set(s); on ? n.add(c.id) : n.delete(c.id); return n; })} />)}</tbody></table>
        {d.coverage?.length > 0 && <>
          <h4 style={{ margin: "14px 0 6px" }}>Coverage by acceptance criterion</h4>
          <table className="dt"><tbody>{d.coverage.map((r) => <tr key={r.ac}><td className="mut">AC {r.ac}</td><td>{r.text}</td>
            <td>{r.types.map((t) => <span key={t} className="pill" style={{ color: TYPE_COLOR[t] }}>{t}</span>)}</td>
            <td>{r.missing.length ? <span style={{ color: "var(--warn)" }}>no {r.missing.join(" / ")} case</span> : "✓"}</td></tr>)}</tbody></table></>}
        <h4 style={{ margin: "14px 0 6px" }}>Sync log</h4>
        {log.length ? <table className="dt"><tbody>{log.slice(0, 40).map((x) => <tr key={x.id}>
          <td className="mut">{ago(new Date(x.at * 1000).toISOString())}</td><td>{x.kind}</td><td>{x.action}</td>
          <td style={{ color: x.ok ? "inherit" : "var(--bad)" }}>{x.ok ? "" : "✗ "}{x.detail}</td><td className="mut">{x.ref}</td></tr>)}</tbody></table>
          : <div className="mut">Nothing sent yet.</div>}
      </div>
    </div>
  );
}

export default function Requirements() {
  const { toast, project, nav, me } = useApp();
  const [q, setQ] = useState(() => store.get("reqQuery", ""));
  const [list, setList] = useState(null);
  const [err, setErr] = useState(null);
  const [key, setKey] = useState(null);
  const [pack, setPack] = useState(null);
  const [designs, setDesigns] = useState([]);
  const [open, setOpen] = useState(null);
  const [useAi, setUseAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const search = async () => {
    setErr(null); store.set("reqQuery", q);
    try { setList(await api(`/api/requirements?q=${encodeURIComponent(q)}`)); } catch (e) { setErr(e.message); setList([]); }
  };
  useEffect(() => { search(); setKey(null); setPack(null); setOpen(null); }, [project]);
  const pick = async (k) => {
    setKey(k); setPack(null); setOpen(null); setBusy(true);
    try { const r = await api(`/api/requirements/${encodeURIComponent(k)}`); setPack(r.pack); setDesigns(r.designs); if (r.designs[0]) setOpen(r.designs[0].id); }
    catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const design = async () => {
    setBusy(true);
    try { const d = await api("/api/designs", { method: "POST", body: { issue_key: key, use_ai: useAi } }); setDesigns((x) => [d, ...x]); setOpen(d.id); toast(`${d.cases.length} test cases written. Review and approve them`); }
    catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const admin = !me || me.role === "admin";
  return (
    <section className="view on">
      <div className="grid2" style={{ gridTemplateColumns: "minmax(260px, 1fr) 2.4fr", alignItems: "start" }}>
        <div className="panel">
          <div className="panel-h"><h3>Stories &amp; bugs</h3></div>
          <div className="panel-b">
            <form className="row" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); search(); }}>
              <input style={{ flex: 1 }} placeholder="Key, text, or JQL" value={q} onChange={(e) => setQ(e.target.value)} />
              <button className="btn sm"><Icon name="search" size={12} /></button></form>
            {err && <div className="hint" style={{ color: "var(--warn)" }}>{err}{admin && <> — <button className="linkish" onClick={() => nav("settings")}>open Integrations</button></>}</div>}
            <div className="prov-list" style={{ marginTop: 8 }}>
              {(list || []).map((i) => (
                <button key={i.key} className={"mrow" + (key === i.key ? " on" : "")} onClick={() => pick(i.key)} style={{ textAlign: "left" }}>
                  <div className="grow"><b>{i.key}</b> {i.title}<small>{i.type} · {i.status}</small></div>
                  {i.design && <span className="slots ok" title="Has test cases">{i.design.cases}</span>}
                </button>))}
              {list && !list.length && !err && <div className="mut">No matches.</div>}
            </div>
          </div>
        </div>
        <div>
          {!key && <div className="empty-state">Pick a story or bug. Autopilot reads it with its acceptance criteria, the linked Confluence pages and the
            pull requests that mention it, then writes positive, negative and boundary test cases — no AI needed.</div>}
          {key && <div className="panel">
            <div className="panel-h"><h3>{pack ? pack.issue.title : key}</h3><span className="spacer" />
              <label className="toggle" title="Also ask the AI for more cases (business rules, edge cases). The rule-based cases are always written."><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /><span className="sw" />AI ideas</label>
              <button className="btn primary sm" disabled={busy || !pack} onClick={design}><Icon name="spark" size={12} />Design test cases</button></div>
            {pack ? <Pack pack={pack} /> : <div className="panel-b mut">{busy ? "Reading the story, its docs and its code…" : ""}</div>}
            {designs.length > 1 && <div className="panel-b row" style={{ gap: 6, flexWrap: "wrap" }}>{designs.map((d) =>
              <button key={d.id} className={"btn sm " + (open === d.id ? "" : "ghost")} onClick={() => setOpen(d.id)}>{new Date(d.created * 1000).toLocaleString()} · {d.cases?.length ?? d.cases} cases</button>)}</div>}
          </div>}
          {open && <div style={{ marginTop: 16 }}><Design key={open} id={open} onClose={() => setOpen(null)} /></div>}
        </div>
      </div>
    </section>
  );
}
