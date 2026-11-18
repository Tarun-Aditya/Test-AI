import { useEffect, useState } from "react";
import { api, art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import { BROWSERS, dataPayload } from "../form.js";
import Icon from "../icons.jsx";
import CodeExplorer from "./CodeExplorer.jsx";
import { ModeBadge, Prov, Status } from "../views/Dashboard.jsx";

const TABS = [["overview", "Overview"], ["code", "Code & files"], ["video", "Video"], ["report", "Report"], ["config", "Data & config"], ["replay", "Replay"]];

function Overview({ r }) {
  const p = r.steps.filter((x) => x.status === "passed").length, f = r.steps.filter((x) => x.status === "failed").length;
  return <>
    <div className="kpis" style={{ marginBottom: 16 }}>
      {[["Steps", r.steps.length], ["Passed", p], ["Failed", f], ["Self-healed", r.healed_steps || 0], ["Duration", secs(r.duration_ms)]].map(([l, v]) =>
        <div className="kpi" key={l}><div className="l">{l}</div><div className="v">{v}</div></div>)}
    </div>
    {r.error && <div className="callout" style={{ background: "var(--bad-soft)", color: "var(--bad)", marginBottom: 14 }}>{r.error}</div>}
    {r.batch && r.batch.length > 1 && <div className="panel" style={{ marginBottom: 14 }}>
      <div className="panel-h"><h3>Iterations in this batch</h3></div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {r.batch.map((b) => <div key={b.run_id} style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <Status s={b.status} /><span className="ellipsis" style={{ flex: 1 }}>{b.test_name}</span><span className="mut">{b.browser}</span>
          {b.run_id === r.run_id ? <span className="pill">this run</span> : <a href={`#/studio/${encodeURIComponent(b.run_id)}`}>open</a>}</div>)}
      </div></div>}
    <div className="panel"><div className="timeline" style={{ maxHeight: "none" }}>
      {r.steps.map((x) => (
        <div key={x.idx} className={"tstep " + x.status}>
          <span className="ic">{x.status === "passed" ? "✓" : x.status === "failed" ? "✕" : "–"}</span>
          <div style={{ minWidth: 0 }}><div className="t1">{x.raw || x.action} <Prov parsed={x.parsed_by} picked={x.picked_by} /></div>
            {x.origin && x.origin !== x.raw && <div className="t2 origin">“{x.origin}”</div>}
            {x.value && <div className="t2" style={{ color: "var(--ok)" }}>value: {x.value}</div>}
            <div className="t2">{x.healed ? <span className="heal">self-healed · </span> : null}{x.locator || x.action} · {secs(x.duration_ms)}</div>
            {x.error && <div className="err">{x.error}</div>}</div>
          {x.screenshot ? <a href={art(r.run_id, x.screenshot)} target="_blank" rel="noreferrer"><img loading="lazy" src={art(r.run_id, x.screenshot)} alt="" /></a> : <span className="noimg" />}
        </div>))}
    </div></div>
  </>;
}

function Config({ r }) {
  const c = r.config || {};
  const rows = [["Mode", r.mode === "replay" ? "Script (replay of generated script)" : r.parser === "rules" ? "English → steps by rule parser (AI off)" : r.parser === "user" ? "Steps edited by hand" : "English → steps by AI (Claude)"], ["Engine", c.engine || r.engine],
    ["Browser", c.browser || r.browser], ["Machine", r.agent], ["Headless", String(c.headless ?? "")], ["Step timeout", c.timeout_ms ? `${c.timeout_ms / 1000}s` : ""],
    ["Video", String(c.record_video ?? "")], ["Self-heal (engine)", String(c.self_heal ?? "")], ["Dialogs", c.dialogs], ["Session", c.session || "—"],
    ["Chrome profile", c.profile_dir || "—"], ["Iteration", r.iteration || "single"], ["Replay of", r.source_run || "—"], ["Run id", r.run_id]];
  return <div className="grid2" style={{ alignItems: "start" }}>
    <div className="panel"><div className="panel-h"><h3>Test data</h3>{r.sensitive.length > 0 && <span className="pill" style={{ color: "var(--warn)" }}>🔒 {r.sensitive.join(", ")}</span>}</div>
      <pre className="code" style={{ margin: 0, borderRadius: "0 0 12px 12px" }}><code>{JSON.stringify(r.data, null, 2)}</code></pre></div>
    <div className="panel"><div className="panel-h"><h3>Configuration</h3></div>
      <table className="table"><tbody>{rows.map(([k, v]) => <tr key={k}><td className="mut" style={{ width: 150 }}>{k}</td><td className="mono">{v}</td></tr>)}</tbody></table></div>
  </div>;
}

function Replay({ r, onDone }) {
  const { machines, health, toast, nav, form } = useApp();
  const [mode, setMode] = useState("same");
  const [json, setJson] = useState(() => JSON.stringify(r.data || {}, null, 2));
  const [agent, setAgent] = useState(r.agent && machines.some((m) => m.name === r.agent && m.online) ? r.agent : "local");
  const [engine, setEngine] = useState(r.config?.engine || r.engine || "playwright");
  const [browser, setBrowser] = useState(r.config?.browser || r.browser || "chromium");
  const [headless, setHeadless] = useState(r.config?.headless ?? true);
  const [heal, setHeal] = useState(true);
  const [busy, setBusy] = useState(false);
  const online = machines.filter((m) => m.online);
  /** Studio's Test data (single run or iterations). Empty secrets are dropped so the run's stored secret is used. */
  const studioData = () => {
    let p;
    try { p = dataPayload(form); } catch (e) { return { error: e.message }; }
    const secret = new Set(p.sensitive);
    const prune = (o, pre = "") => Object.fromEntries(Object.entries(o || {}).flatMap(([k, v]) => {
      const key = pre + k;
      if (v && typeof v === "object" && !Array.isArray(v)) return [[k, prune(v, key + ".")]];
      return secret.has(key) && (v === "" || v == null) ? [] : [[k, v]];
    }));
    return { data: prune(p.data), sensitive: p.sensitive,
      iterations: p.iterations?.map((it) => ({ ...it, data: prune(it.data), engine: null })) };
  };
  const studio = mode === "studio" ? studioData() : null;
  const go = async () => {
    setBusy(true);
    try {
      let data = null, sensitive = r.sensitive, iterations;
      if (mode === "new") { try { data = JSON.parse(json); } catch (e) { throw new Error("Data is not valid JSON: " + e.message); } }
      if (mode === "studio") {
        const p = studioData();
        if (p.error) throw new Error(p.error);
        ({ data, iterations } = p);
        sensitive = [...new Set([...r.sensitive, ...p.sensitive])];
      }
      const config = { ...(r.config || {}), engine: engine === "both" ? "playwright" : engine, browser, headless, self_heal: heal, live_view: true, session: r.config?.session || null, save_session: null };
      const res = await api("/api/runs", { method: "POST", body: { replay_run_id: r.run_id, data, sensitive, iterations, config, agent, reuse_secrets: true,
        engines: engine === "both" ? ["playwright", "selenium"] : null } });
      onDone(); nav("studio", res.run_ids[0]); toast(res.run_ids.length > 1 ? `Replay started: ${res.run_ids.length} iterations` : "Replay started");
    } catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  return <div style={{ maxWidth: 720 }}>
    <p className="hint" style={{ marginTop: 0 }}>Replays this run's <b>generated script</b> (Script mode): stored native locators, no AI parsing.
      If a locator no longer matches, the engine self-heals and the new run generates a fresh script.</p>
    <div className="field"><span>Test data</span>
      <div className="seg"><button className={mode === "same" ? "on" : ""} onClick={() => setMode("same")}>Same data as this run</button>
        <button className={mode === "new" ? "on" : ""} onClick={() => setMode("new")}>Edit / new data</button>
        <button className={mode === "studio" ? "on" : ""} onClick={() => setMode("studio")}>Studio's test data</button></div>
      {mode === "studio" ? <small>{studio.error ? <span style={{ color: "var(--bad)" }}>{studio.error}</span>
        : studio.iterations ? <>Runs <b>{studio.iterations.length} iteration{studio.iterations.length > 1 ? "s" : ""}</b> from the Studio's Iterations grid
          ({studio.iterations.map((it) => it.browser || browser).join(", ")}), each replaying this script. Edit them under Studio → Test data.</>
        : <>Uses the Studio's <b>Single run</b> values ({Object.keys(studio.data || {}).join(", ") || "none"}). Keys you don't set keep this run's values.</>}
        {" "}Secrets left empty there reuse this run's stored secret.</small>
      : mode === "same" ? <small>{r.has_secrets ? "Sensitive values are reused from the hub's vault for this run." : r.sensitive.length ? "Sensitive values were not kept. They come from AIAUTO_SECRET_* variables on the machine." : "Exactly the values shown under Data & config."}</small>
        : <><textarea className="mono" rows={10} value={json} onChange={(e) => setJson(e.target.value)} />
          <small>Values left as •••••• keep the original secret. Add a "rows" list to replay several iterations.</small></>}
    </div>
    <div className="grid2">
      <div className="field"><span>Machine</span><select value={agent} onChange={(e) => setAgent(e.target.value)}>
        <option value="local">{health.local_name} (this server)</option>{online.length > 0 && <option value="any">Any free machine</option>}
        {online.map((m) => <option key={m.name} value={m.name}>{m.name}{m.busy ? " · busy" : ""}</option>)}</select></div>
      <div className="field"><span>Engine</span><div className="seg">{[["playwright", "Playwright"], ["selenium", "Selenium"], ["both", "Both"]].map(([e, l]) =>
        <button key={e} className={engine === e ? "on" : ""} onClick={() => setEngine(e)}>{l}</button>)}</div></div>
      <div className="field"><span>Browser</span><select value={browser} onChange={(e) => setBrowser(e.target.value)}>{BROWSERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
      <div className="field"><span>Options</span><div style={{ display: "flex", gap: 16, paddingTop: 6 }}>
        <label className="toggle"><input type="checkbox" checked={headless} onChange={(e) => setHeadless(e.target.checked)} /><span className="sw" />Headless</label>
        <label className="toggle"><input type="checkbox" checked={heal} onChange={(e) => setHeal(e.target.checked)} /><span className="sw" />Self-heal</label></div></div>
    </div>
    <button className="btn primary" disabled={busy} onClick={go}><Icon name="play" size={14} />Replay script</button>
  </div>;
}

export default function RunDrawer({ id, tab: initialTab, onClose }) {
  const { toast, loadLibrary, loadRuns } = useApp();
  const [r, setR] = useState(null);
  const [tab, setTab] = useState(initialTab || "overview");
  useEffect(() => { setTab(initialTab || "overview"); }, [id, initialTab]);
  useEffect(() => { api(`/api/runs/${encodeURIComponent(id)}`).then(setR).catch((e) => { toast(e.message, true); onClose(); }); }, [id]);
  useEffect(() => { const h = (e) => e.key === "Escape" && onClose(); document.addEventListener("keydown", h); return () => document.removeEventListener("keydown", h); }, [onClose]);
  const saveLib = async () => {
    const name = prompt("Save the generated script to the library as:", (r.test_name || "").replace(/\s*\[#.*\]$/, ""));
    if (name === null) return;
    try { await api("/api/library/from-run", { method: "POST", body: { run_id: r.run_id, name: name || null } }); loadLibrary(); toast("Saved to library"); } catch (e) { toast(e.message, true); }
  };
  const del = async () => { if (!confirm("Delete this run and its artifacts?")) return; await api(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }); loadRuns(); onClose(); };
  const done = r && !isLive(r);
  return <>
    <div className="scrim on" onClick={onClose} />
    <aside className="drawer on" aria-label="Run details">
      <div className="drawer-h">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="ellipsis">{r ? r.test_name : "Loading…"}</h2>
          {r && <div className="mut" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 4 }}>
            <Status s={r.status} /><ModeBadge mode={r.mode} parser={r.parser} />{r.engine}{r.browser ? ` / ${r.browser}` : ""} · {r.agent} · {(r.started_at || "").replace("T", " ").slice(0, 19)}</div>}
        </div>
        {done && <div className="links">
          <a href={`/api/runs/${encodeURIComponent(id)}/export`}><Icon name="up" size={12} style={{ transform: "rotate(180deg)" }} /> Export project</a>
          <a href={`/api/runs/${encodeURIComponent(id)}/download`}>Artifacts zip</a>
          <button onClick={saveLib}>＋ Library</button>
          <button onClick={() => setTab("replay")}>▶ Replay</button>
          <button onClick={del} style={{ color: "var(--bad)" }}>Delete</button></div>}
        <button className="x" onClick={onClose} title="Close"><Icon name="x" /></button>
      </div>
      <div className="tabs" style={{ background: "var(--panel)" }}>
        {TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      <div className={"drawer-b" + (tab === "code" ? " flush" : "")}>
        {!r ? <div className="empty-state">Loading…</div> : isLive(r) ? <div className="empty-state">This run is still in progress. <a href={`#/studio/${encodeURIComponent(r.run_id)}`} onClick={onClose}>Watch it live</a></div> : <>
          {tab === "overview" && <Overview r={r} />}
          {tab === "code" && <CodeExplorer runId={r.run_id} />}
          {tab === "video" && (r.video ? <video controls autoPlay muted src={art(r.run_id, r.video)} /> : <div className="empty-state">No video was recorded for this run.</div>)}
          {tab === "report" && <iframe src={art(r.run_id, "report.html")} title="Report" />}
          {tab === "config" && <Config r={r} />}
          {tab === "replay" && <Replay r={r} onDone={onClose} />}
        </>}
      </div>
    </aside>
  </>;
}
