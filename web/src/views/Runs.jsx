import { useState } from "react";
import { art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";
import { ModeBadge, Status, StepBar, StopButton } from "./Dashboard.jsx";

export default function Runs() {
  const { runs, openRun, nav, loadRuns } = useApp();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const rows = runs.filter((r) => (!status || r.status === status) && (!mode || (mode === "script" ? r.mode === "replay" : r.mode !== "replay" && (r.parser || "ai") === mode))
    && (!q || `${r.test_name} ${r.agent} ${r.engine} ${r.browser}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <section className="view on">
      <div className="toolbar">
        <label className="search"><Icon name="search" size={16} /><input className="in" placeholder="Filter by name, machine, engine…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <div className="seg">{[["", "All"], ["passed", "Passed"], ["failed", "Failed"], ["running", "Running"], ["interrupted", "Interrupted"]].map(([k, l]) =>
          <button key={k} className={status === k ? "on" : ""} onClick={() => setStatus(k)}>{l}</button>)}</div>
        <div className="seg">{[["", "Any mode"], ["ai", "AI"], ["rules", "Rules"], ["script", "Script"]].map(([k, l]) =>
          <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{l}</button>)}</div>
        <span className="spacer" />
        <button className="btn" onClick={loadRuns}><Icon name="refresh" size={15} />Refresh</button>
      </div>
      <div className="panel" style={{ overflow: "auto" }}>
        <table className="table">
          <thead><tr><th>Test</th><th>Status</th><th>Mode</th><th>Engine · browser · machine</th><th style={{ width: 160 }}>Steps</th><th>Duration</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.run_id} className="clickable" onClick={() => (isLive(r) ? nav("studio", r.run_id) : openRun(r.run_id))}>
                <td><b style={{ fontWeight: 600 }}>{r.test_name}</b><div className="mut">{(r.started_at || "").replace("T", " ").slice(0, 19)}{r.healed_steps ? <span style={{ color: "var(--warn)" }}> · {r.healed_steps} healed</span> : null}</div></td>
                <td><Status s={r.status} /></td>
                <td><ModeBadge mode={r.mode} parser={r.parser} /></td>
                <td>{r.engine}{r.browser ? ` · ${r.browser}` : ""}<div className="mut">{r.agent}</div></td>
                <td><StepBar r={r} /><div className="mut" style={{ marginTop: 3 }}>{isLive(r) ? `${r.done || 0}/${r.total}` : `${r.passed_steps}/${r.total} passed`}</div></td>
                <td className="mono">{secs(r.duration_ms)}</td>
                <td onClick={(e) => e.stopPropagation()}><div className="links" style={{ flexWrap: "nowrap" }}>
                  {isLive(r) ? <><button onClick={() => nav("studio", r.run_id)}>● Watch</button><StopButton ids={[r.run_id]} /></> : <>
                    <a href={art(r.run_id, "report.html")} target="_blank" rel="noreferrer">Report</a>
                    {r.video && <button onClick={() => openRun(r.run_id, "video")}>Video</button>}
                    <button onClick={() => openRun(r.run_id, "code")}>Code</button>
                    <button className="accent" onClick={() => openRun(r.run_id, "replay")}>▶ Replay</button></>}
                </div></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={7}><div className="empty-state">No runs match.</div></td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
