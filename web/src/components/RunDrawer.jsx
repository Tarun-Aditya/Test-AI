import { useEffect, useState } from "react";
import { api, art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import { BROWSERS, dataPayload } from "../form.js";
import Icon from "../icons.jsx";
import CodeExplorer from "./CodeExplorer.jsx";
import { ModeBadge, Prov, Status, StopButton } from "../views/Dashboard.jsx";

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


/** A failed (or passed) visual check: the baseline, this run's picture and what differs, with a button to accept the new look. */
function VisualCheck({ r, step }) {
  const { can, toast, ask } = useApp();
  const [items, setItems] = useState(null);
  const name = ((step.value || "").split(":")[0] || "").trim();
  const load = () => api(`/api/runs/${encodeURIComponent(r.run_id)}/visual`).then(setItems).catch(() => setItems([]));
  useEffect(() => { setItems(null); load(); }, [r.run_id, step.idx]);
  const list = items || [];
  const one = list.find((x) => x.name === name) || (list.length === 1 ? list[0] : null);
  if (!one) return items ? <div className="callout" style={{ margin: 12 }}>No picture was kept for this visual check.</div> : null;
  const approve = async () => {
    if (!(await ask({ title: `Accept the new look of “${one.name}”?`, body: "This run's picture becomes the baseline. Later runs are compared with it.", ok: "Accept new look" }))) return;
    try { await api("/api/baselines/approve", { method: "POST", body: { run_id: r.run_id, name: one.name } }); toast("Baseline updated"); load(); } catch (e) { toast(e.message, true); }
  };
  const pic = (title, src, extra) => <figure className="vpic"><figcaption>{title}</figcaption>{src ? <a href={src} target="_blank" rel="noreferrer"><img loading="lazy" src={src} alt={`${title} of ${one.name}`} /></a> : <div className="empty-state">{extra}</div>}</figure>;
  return (
    <div className="visual" role="region" aria-label="Visual check">
      <div className="row" style={{ marginBottom: 8 }}>
        <b>Visual check “{one.name}”</b>
        <span className={"status " + (one.different ? "failed" : "passed")}>{one.different ? "looks different" : "matches"}</span>
        <span className="spacer" />
        {one.different && can.run && <button className="btn sm primary" onClick={approve}>Accept the new look</button>}
      </div>
      <div className="vgrid">
        {pic("Baseline", one.baseline, "No baseline yet")}
        {pic("This run", art(r.run_id, one.actual))}
        {one.different && pic("Difference (red)", art(r.run_id, one.diff))}
      </div>
    </div>
  );
}

/** Facts, step list and the screenshot of the selected step: the core of a run, on its page and in the Inbox. */
export function StepsView({ r, compact }) {
  const firstBad = r.steps.find((x) => x.status === "failed");
  const [sel, setSel] = useState(firstBad ? firstBad.idx : r.steps.length ? r.steps[r.steps.length - 1].idx : 0);
  const [loc, setLoc] = useState(false);
  useEffect(() => { const b = r.steps.find((x) => x.status === "failed"); setSel(b ? b.idx : r.steps.length ? r.steps[r.steps.length - 1].idx : 0); }, [r.run_id]);
  const p = r.steps.filter((x) => x.status === "passed").length;
  const step = r.steps.find((x) => x.idx === sel);
  const pos = r.steps.findIndex((x) => x.idx === sel);
  const move = (d) => { const n = r.steps[pos + d]; if (n) setSel(n.idx); };
  const parsed = { ai: "AI", rules: "Rules", user: "Edited by hand" }[r.parser] || (r.mode === "replay" ? "Script replay" : "AI");
  return <>
    {!compact && <div className="facts" role="list" aria-label="Summary">
      <div className="fact" role="listitem"><span>Steps</span><b>{p} <small>of {r.steps.length} passed</small></b></div>
      <div className="fact" role="listitem"><span>{firstBad ? "Failed at" : "Result"}</span><b>{firstBad ? `Step ${firstBad.idx + 1}` : r.status === "passed" ? "All passed" : r.status}</b></div>
      <div className="fact" role="listitem"><span>Duration</span><b>{secs(r.duration_ms)}</b></div>
      <div className="fact" role="listitem"><span>Self-healed</span><b>{r.healed_steps || 0}</b></div>
      <div className="fact" role="listitem"><span>Steps made by</span><b>{r.mode === "replay" ? "Script" : parsed}</b></div>
    </div>}
    {r.error && !firstBad && <div className="callout bad">{r.error}</div>}
    <div className="runsplit">
      <section className="panel" aria-label="Steps" style={{ overflow: "hidden" }}>
        <div className="panel-h"><h3>Steps</h3><span className="spacer" />
          <label className="toggle"><input type="checkbox" checked={loc} onChange={(e) => setLoc(e.target.checked)} /><span className="sw" />Show locators</label></div>
        <div className="steplist">
          {r.steps.map((x) => (
            <button key={x.idx} type="button" className={"srow " + x.status + (x.idx === sel ? " on" : "")} aria-current={x.idx === sel ? "step" : undefined} onClick={() => setSel(x.idx)}>
              <span className="n">{x.idx + 1}</span><StepIcon s={x.status} />
              <span className="sbody"><span className="stext">{x.raw || x.action}</span>
                {x.origin && x.origin !== x.raw && <span className="ssub origin">“{x.origin}”</span>}
                {x.value && <span className="ssub">{x.value}</span>}
                {x.healed ? <span className="ssub" style={{ color: "var(--warn)" }}>self-healed</span> : null}
                {loc && <span className="ssub mono">{x.locator || x.action} <Prov parsed={x.parsed_by} picked={x.picked_by} /></span>}
                {x.error && <span className="serr">{x.error}</span>}</span>
              <span className="sdur mono">{x.status === "skipped" ? "–" : secs(x.duration_ms)}</span>
            </button>))}
          {!r.steps.length && <div className="empty-state">No steps ran.</div>}
        </div>
      </section>
      <section className="panel shotpanel" aria-label="Screenshot">
        <div className="panel-h"><h3>{step ? `Step ${step.idx + 1}` : "Screenshot"}</h3><span className="spacer" />
          <button className="btn sm" aria-label="Previous step" disabled={pos <= 0} onClick={() => move(-1)}><Icon name="back" size={14} /></button>
          <span className="mut">{pos + 1} / {r.steps.length}</span>
          <button className="btn sm" aria-label="Next step" disabled={pos >= r.steps.length - 1} onClick={() => move(1)}><Icon name="chev" size={14} /></button>
          {step?.screenshot && <a className="btn sm" href={art(r.run_id, step.screenshot)} target="_blank" rel="noreferrer">Full size</a>}</div>
        <div className="shot">
          <div className="chrome"><div className="lights"><i /><i /><i /></div><div className="urlbar"><span>{step?.url || "—"}</span></div></div>
          {step?.screenshot ? <img src={art(r.run_id, step.screenshot)} alt={`Screenshot after step ${step.idx + 1}: ${step.raw || step.action}`} />
            : <div className="empty-state">No screenshot for this step.</div>}
        </div>
        {step && <dl className="kv">
          <dt>Step</dt><dd>{step.raw || step.action}</dd>
          <dt>Result</dt><dd><Status s={step.status} />{step.duration_ms ? <span className="mut"> · {secs(step.duration_ms)}</span> : null}</dd>
          {step.locator && <><dt>Element</dt><dd className="mono">{step.locator}</dd></>}
          {step.dialog && <><dt>Dialog</dt><dd>{step.dialog}</dd></>}
        </dl>}
        {step?.action === "assert_visual" && <VisualCheck r={r} step={step} />}
      </section>
    </div>
    {r.batch && r.batch.length > 1 && !compact && <section className="panel" style={{ marginTop: 16 }}>
      <div className="panel-h"><h3>Other runs in this launch</h3><span className="mut">{r.batch.length}</span></div>
      <div>{r.batch.map((b) => <a key={b.run_id} className="lrow" href={`#/runs/${encodeURIComponent(b.run_id)}`}>
        <Status s={b.status} /><span className="ellipsis" style={{ flex: 1 }}>{b.test_name}</span><span className="mut">{b.browser}</span>
        {b.run_id === r.run_id && <span className="pill">this run</span>}</a>)}</div></section>}
  </>;
}

export function StepIcon({ s }) {
  const n = { passed: "ok", failed: "fail", skipped: "minus", running: "clock", pending: "clock" }[s] || "minus";
  return <span className={"sic " + s} aria-label={s}><Icon name={n} size={16} /></span>;
}

function Menu({ label, icon, children }) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (!open) return; const h = () => setOpen(false); setTimeout(() => document.addEventListener("click", h, { once: true }), 0); return () => document.removeEventListener("click", h); }, [open]);
  return <div className="menu">
    <button className="btn" aria-haspopup="true" aria-expanded={open} aria-label={label} onClick={() => setOpen(!open)}><Icon name={icon} size={15} />{icon !== "more" && label}</button>
    {open && <div className="menu-pop" role="menu">{children}</div>}
  </div>;
}

const PAGE_TABS = [["steps", "Steps"], ["video", "Video"], ["code", "Scripts & files"], ["report", "Report"], ["config", "Data & settings"], ["replay", "Replay"]];

/** One run as a page (#/runs/<id>[/tab]); `embedded` = the Inbox's right pane. */
export default function RunPage({ id, tab: tabFromUrl, embedded }) {
  const { toast, loadLibrary, loadRuns, can, nav, subscribe } = useApp();
  const [r, setR] = useState(null);
  const [err, setErr] = useState(null);
  const tab = PAGE_TABS.some(([k]) => k === tabFromUrl) && (tabFromUrl !== "replay" || can.run) ? tabFromUrl : "steps";
  const setTab = (t) => (embedded ? nav("runs", id, t) : nav("runs", id, t === "steps" ? null : t));
  const load = () => api(`/api/runs/${encodeURIComponent(id)}`).then((x) => { setR(x); setErr(null); }).catch((e) => setErr(e.message));
  useEffect(() => { setR(null); load(); }, [id]);
  useEffect(() => subscribe((e) => { if (e.run_id === id && ["run_finished", "artifacts_uploaded"].includes(e.type)) load(); }), [id]);
  const saveLib = async () => {
    const name = prompt("Save the generated script to the library as:", (r.test_name || "").replace(/\s*\[#.*\]$/, ""));
    if (name === null) return;
    try { await api("/api/library/from-run", { method: "POST", body: { run_id: r.run_id, name: name || null } }); loadLibrary(); toast("Saved to library"); } catch (e) { toast(e.message, true); }
  };
  const del = async () => {
    if (!confirm("Delete this run with its report, video and screenshots? This can't be undone.")) return;
    await api(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }); loadRuns(); toast("Run deleted"); nav("runs");
  };
  if (err) return <section className="view on"><div className="empty-state">{err}. <a href="#/runs">Back to runs</a></div></section>;
  if (!r) return <section className="view on"><div className="skel" style={{ height: 28, width: 320 }} /><div className="skel" style={{ height: 380, marginTop: 16 }} /></section>;
  const done = !isLive(r);
  const when = (r.started_at || "").replace("T", " ").slice(0, 16);
  return <section className={"view on runpage" + (embedded ? " embedded" : "")}>
    <div className="runhead">
      <div className="row" style={{ gap: 10 }}>
        <h2 className={embedded ? "" : "big"}>{r.test_name}</h2><Status s={r.status} />
        <span className="spacer" />
        {done && <>
          {embedded && <a className="btn" href={`#/runs/${encodeURIComponent(r.run_id)}`}>Open page</a>}
          <a className="btn" href={art(r.run_id, "report.html")} target="_blank" rel="noreferrer"><Icon name="doc" size={15} />Report</a>
          {!embedded && <Menu label="Download" icon="download">
            <a role="menuitem" href={`/api/runs/${encodeURIComponent(id)}/export`}>Project (scripts + framework)</a>
            <a role="menuitem" href={`/api/runs/${encodeURIComponent(id)}/download`}>All artifacts (zip)</a>
          </Menu>}
          {!embedded && <Menu label="More actions" icon="more">
            {can.run && <button role="menuitem" onClick={saveLib}>Save script to library</button>}
            {can.run && <button role="menuitem" className="danger" onClick={del}>Delete run…</button>}
          </Menu>}
          {can.run && <button className="btn primary" onClick={() => setTab("replay")}><Icon name="play" size={13} />Replay</button>}
        </>}
        {!done && <><a className="btn" href={`#/studio/${encodeURIComponent(r.run_id)}`}>Watch live</a>{can.run && <StopButton ids={[r.run_id]} className="btn" />}</>}
      </div>
      <div className="meta">
        <ModeBadge mode={r.mode} parser={r.parser} />
        <span className="mono">{r.engine}{r.browser ? ` · ${r.browser}` : ""}{r.config?.headless === false ? " · headed" : ""}</span>
        <span>{r.agent}</span><span>{when}</span>{r.owner && <span>by {r.owner}</span>}
        {r.iteration && <span>iteration {r.iteration}</span>}{r.source_run && <a href={`#/runs/${encodeURIComponent(r.source_run)}`}>replay of an earlier run</a>}
      </div>
      {!embedded && <nav className="tabs" aria-label="Run sections">
        {PAGE_TABS.filter(([k]) => can.run || k !== "replay").map(([k, l]) =>
          <button key={k} className={tab === k ? "on" : ""} aria-current={tab === k ? "page" : undefined} onClick={() => setTab(k)}>{l}</button>)}
      </nav>}
    </div>
    {!done ? <div className="empty-state">This run is still in progress. <a href={`#/studio/${encodeURIComponent(r.run_id)}`}>Watch it live</a>.</div> : <>
      {(embedded || tab === "steps") && <StepsView r={r} compact={embedded} />}
      {!embedded && tab === "code" && <div className="panel" style={{ overflow: "hidden" }}><CodeExplorer runId={r.run_id} /></div>}
      {!embedded && tab === "video" && (r.video ? <video className="runvideo" controls autoPlay muted src={art(r.run_id, r.video)} /> : <div className="empty-state">No video was recorded for this run. Turn on “Record video” in Studio → Settings.</div>)}
      {!embedded && tab === "report" && <iframe className="runreport" src={art(r.run_id, "report.html")} title="Report" />}
      {!embedded && tab === "config" && <Config r={r} />}
      {!embedded && tab === "replay" && <div className="panel"><div className="panel-b"><Replay r={r} onDone={() => {}} /></div></div>}
    </>}
  </section>;
}
