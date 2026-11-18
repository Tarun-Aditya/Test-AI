import { useState } from "react";
import { api, art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";
import { ModeBadge, Status, StepBar, StopButton } from "./Dashboard.jsx";

const PAGE = 60;

function dayOf(iso) {
  if (!iso) return "Earlier";
  const d = new Date(iso), today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

/** A launch's name: the part its runs' names share ("SHOP-1 · …" -> "SHOP-1"), else the first name. */
function launchName(rows) {
  const names = rows.map((r) => r.test_name || "");
  let p = names[0] || "";
  for (const n of names) while (p && !n.startsWith(p)) p = p.slice(0, -1);
  p = p.replace(/[\s·#\-–[(]+$/, "").trim();
  return p.length >= 3 ? p : names[0];
}

/** Rows -> [[day, [item]]], an item being one run or a launch {batch, runs}. */
function group(rows) {
  const days = [];
  const byBatch = new Map();
  for (const r of rows) {
    const day = dayOf(r.started_at);
    let d = days[days.length - 1];
    if (!d || d[0] !== day) days.push((d = [day, []]));
    if (r.batch_id) {
      const k = day + "|" + r.batch_id;
      if (byBatch.has(k)) { byBatch.get(k).runs.push(r); continue; }
      const g = { batch: r.batch_id, runs: [r] };
      byBatch.set(k, g); d[1].push(g);
    } else d[1].push(r);
  }
  return days;
}

function RunRow({ r, child, sel, onSel }) {
  const { openRun, nav, can } = useApp();
  const open = () => (isLive(r) ? nav("studio", r.run_id) : openRun(r.run_id));
  return (
    <tr className={"clickable" + (child ? " child" : "")} onClick={open}>
      <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${r.test_name}`} checked={sel} onChange={(e) => onSel(e.target.checked)} /></td>
      <td><a href={`#/runs/${encodeURIComponent(r.run_id)}`} onClick={(e) => e.stopPropagation()} className="rname">{child ? r.test_name.replace(/^[^·]+·\s*/, "") : r.test_name}</a>
        {r.healed_steps ? <div className="mut" style={{ color: "var(--warn)" }}>{r.healed_steps} self-healed</div> : null}</td>
      <td><Status s={r.status} /></td>
      <td style={{ minWidth: 120 }}><StepBar r={r} /><div className="mut" style={{ marginTop: 3 }}>{isLive(r) ? `${r.done || 0}/${r.total}` : `${r.passed_steps}/${r.total}`}</div></td>
      <td><ModeBadge mode={r.mode} parser={r.parser} /></td>
      <td className="nowrap mut">{r.engine}{r.browser ? ` · ${r.browser}` : ""}</td>
      <td className="nowrap mut">{r.agent}</td>
      <td className="mono nowrap">{secs(r.duration_ms)}</td>
      <td className="mut nowrap">{(r.started_at || "").slice(11, 16)}</td>
      <td onClick={(e) => e.stopPropagation()}><div className="rowacts">
        {isLive(r) ? <><button className="btn sm" onClick={() => nav("studio", r.run_id)}>Watch</button>{can.run && <StopButton ids={[r.run_id]} className="btn sm" />}</> : <>
          <a className="btn sm" href={art(r.run_id, "report.html")} target="_blank" rel="noreferrer">Report</a>
          {can.run && <button className="btn sm" onClick={() => openRun(r.run_id, "replay")} aria-label={`Replay ${r.test_name}`}><Icon name="play" size={11} />Replay</button>}</>}
      </div></td>
    </tr>
  );
}

function LaunchRows({ g, sel, setSel }) {
  const [open, setOpen] = useState(g.runs.some((r) => r.status === "failed" || isLive(r)));
  const runs = g.runs;
  const failed = runs.filter((r) => r.status === "failed").length, passed = runs.filter((r) => r.status === "passed").length, live = runs.filter(isLive).length;
  const engines = [...new Set(runs.map((r) => r.engine))].join(" + "), agents = [...new Set(runs.map((r) => r.agent))].join(", ");
  const dur = runs.reduce((a, r) => a + (r.duration_ms || 0), 0);
  const all = runs.every((r) => sel.has(r.run_id));
  return <>
    <tr className="grp clickable" onClick={() => setOpen(!open)}>
      <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select this launch" checked={all}
        onChange={(e) => setSel((s) => { const n = new Set(s); runs.forEach((r) => (e.target.checked ? n.add(r.run_id) : n.delete(r.run_id))); return n; })} /></td>
      <td><button className="caretbtn" aria-expanded={open} aria-label={open ? "Collapse launch" : "Expand launch"} onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
        <Icon name={open ? "chevd" : "chev"} size={14} /></button><b>{launchName(runs)}</b> <span className="mut">{runs.length} runs</span></td>
      <td>{live ? <Status s="running" /> : failed ? <span className="status failed"><Icon name="fail" size={13} />{failed} failed</span> : <span className="status passed"><Icon name="ok" size={13} />all passed</span>}</td>
      <td><div className="sbar"><i className="p" style={{ width: `${(100 * passed) / runs.length}%` }} /><i className="f" style={{ width: `${(100 * failed) / runs.length}%` }} /></div>
        <div className="mut" style={{ marginTop: 3 }}>{passed}/{runs.length} passed</div></td>
      <td><ModeBadge mode={runs[0].mode} parser={runs[0].parser} /></td>
      <td className="nowrap mut">{engines}</td><td className="nowrap mut">{agents}</td>
      <td className="mono nowrap">{secs(dur)}</td><td className="mut nowrap">{(runs[runs.length - 1].started_at || "").slice(11, 16)}</td><td />
    </tr>
    {open && runs.map((r) => <RunRow key={r.run_id} r={r} child sel={sel.has(r.run_id)} onSel={(on) => setSel((s) => { const n = new Set(s); on ? n.add(r.run_id) : n.delete(r.run_id); return n; })} />)}
  </>;
}

export default function Runs() {
  const { runs, loadRuns, can, toast, me } = useApp();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const [mine, setMine] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [sel, setSel] = useState(new Set());
  const rows = runs.filter((r) => (!status || (status === "running" ? isLive(r) : r.status === status))
    && (!mode || (mode === "script" ? r.mode === "replay" : r.mode !== "replay" && (r.parser || "ai") === mode))
    && (!mine || r.owner === me?.username)
    && (!q || `${r.test_name} ${r.agent} ${r.engine} ${r.browser} ${r.error || ""}`.toLowerCase().includes(q.toLowerCase())));
  const shown = rows.slice(0, limit);
  const days = group(shown);
  const pick = (r) => (on) => setSel((s) => { const n = new Set(s); on ? n.add(r.run_id) : n.delete(r.run_id); return n; });
  const delSel = async () => {
    if (!confirm(`Delete ${sel.size} run(s) with their reports and videos? This can't be undone.`)) return;
    for (const id of sel) await api(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
    setSel(new Set()); loadRuns(); toast("Deleted");
  };
  const liveSel = runs.filter((r) => sel.has(r.run_id) && isLive(r)).map((r) => r.run_id);
  return (
    <section className="view on">
      <div className="toolbar">
        <label className="search"><Icon name="search" size={16} /><span className="sr">Search runs</span><input className="in" placeholder="Search name, machine, engine, error…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <div className="seg" role="group" aria-label="Status">{[["", "All"], ["failed", "Failed"], ["running", "Running"], ["passed", "Passed"], ["interrupted", "Interrupted"]].map(([k, l]) =>
          <button key={k} className={status === k ? "on" : ""} aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}</div>
        <div className="seg" role="group" aria-label="How the steps were made">{[["", "Any source"], ["ai", "AI"], ["rules", "Rules"], ["script", "Script"]].map(([k, l]) =>
          <button key={k} className={mode === k ? "on" : ""} aria-pressed={mode === k} onClick={() => setMode(k)}>{l}</button>)}</div>
        {me && <label className="toggle"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /><span className="sw" />Mine</label>}
        <span className="spacer" />
        <button className="btn" onClick={loadRuns}><Icon name="refresh" size={15} />Refresh</button>
      </div>
      {sel.size > 0 && <div className="bulk" role="region" aria-label="Selected runs">
        <b>{sel.size} selected</b><span className="spacer" />
        {liveSel.length > 0 && can.run && <StopButton ids={liveSel} label={`Stop ${liveSel.length}`} className="btn sm" />}
        {can.run && <button className="btn sm danger" onClick={delSel}>Delete</button>}
        <button className="btn sm ghost" onClick={() => setSel(new Set())}>Clear</button>
      </div>}
      {days.map(([day, items]) => (
        <div key={day} className="dayblock">
          <div className="dayh">{day}</div>
          <div className="panel" style={{ overflowX: "auto" }}>
            <table className="table runs">
              <thead><tr><th style={{ width: 34 }}><span className="sr">Select</span></th><th>Run</th><th>Status</th><th>Steps</th><th>Source</th><th>Engine · browser</th><th>Machine</th><th>Duration</th><th>Started</th><th><span className="sr">Actions</span></th></tr></thead>
              <tbody>{items.map((it) => it.batch
                ? <LaunchRows key={it.batch} g={it} sel={sel} setSel={setSel} />
                : <RunRow key={it.run_id} r={it} sel={sel.has(it.run_id)} onSel={pick(it)} />)}</tbody>
            </table>
          </div>
        </div>))}
      {!rows.length && <div className="panel"><div className="empty-state">{runs.length ? "No runs match these filters." : <>No runs yet. <a href="#/studio">Start a test</a></>}</div></div>}
      {rows.length > shown.length && <div className="row" style={{ justifyContent: "center", marginTop: 12 }}>
        <span className="mut">Showing {shown.length} of {rows.length}</span><button className="btn sm" onClick={() => setLimit(limit + PAGE)}>Show {Math.min(PAGE, rows.length - shown.length)} more</button></div>}
    </section>
  );
}
