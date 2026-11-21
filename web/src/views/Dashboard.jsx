import { useEffect, useState } from "react";
import { ago, api, art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";
import { Dots } from "./Automation.jsx";

export function StepBar({ r }) {
  const t = r.total || 1;
  const pending = Math.max(0, (r.done || 0) - (r.passed_steps || 0) - (r.failed_steps || 0));
  return (
    <div className="sbar" title={`${r.passed_steps || 0} passed · ${r.failed_steps || 0} failed · ${r.total || 0} steps`}>
      <i className="p" style={{ width: `${(100 * (r.passed_steps || 0)) / t}%` }} />
      <i className="f" style={{ width: `${(100 * (r.failed_steps || 0)) / t}%` }} />
      {isLive(r) && <i className="r" style={{ width: `${(100 * pending) / t}%` }} />}
    </div>
  );
}

const MODES = {
  ai: ["AI", "ai", "English broken into steps by AI (Claude)"],
  rules: ["Rules", "rules", "English broken into steps by the built-in rule parser (AI off)"],
  user: ["Edited", "rules", "Steps reviewed or edited by hand before running"],
  script: ["Script", "script", "Replayed a generated script: stored native locators, no AI parsing"],
};
export function ModeBadge({ mode, parser }) {
  const key = mode === "replay" ? "script" : parser || "ai";
  const [label, cls, title] = MODES[key] || MODES.ai;
  return <span className={"mode " + cls} title={title}>{label}</span>;
}

/** Who did what for one step: AI or rules split the English, AI or heuristics picked the element. */
export function Prov({ parsed, picked }) {
  const P = { ai: "AI", rules: "rules", user: "edited" };
  const K = { ai: "AI", heuristic: "heuristic", script: "script locator", fallback: "fallback locator", "ai-vision": "AI · screenshot" };
  return <>
    {parsed && <span className={"prov " + (parsed === "ai" ? "ai" : "")} title="Who turned the English into this step">step: {P[parsed] || parsed}</span>}
    {picked && <span className={"prov " + (picked === "ai" || picked === "ai-vision" ? "ai" : "")} title="How the element was identified">element: {K[picked] || picked}</span>}
  </>;
}

/** Stop queued / running runs: queued ones never start, running ones stop after their current step. */
export function StopButton({ ids, label = "Stop", className = "", all }) {
  const { toast, loadRuns } = useApp();
  const [busy, setBusy] = useState(false);
  const go = async (e) => {
    e.stopPropagation();
    if (all && !window.confirm(`Stop ${ids.length} run${ids.length > 1 ? "s" : ""}?`)) return;
    setBusy(true);
    try {
      const r = await api("/api/runs/cancel", { method: "POST", body: { run_ids: ids } });
      const n = Object.values(r.cancelled).filter((v) => v !== "not running").length;
      toast(n ? `Stopping ${n} run${n > 1 ? "s" : ""}` : "Nothing to stop");
      loadRuns();
    } catch (err) { toast(err.message, true); } finally { setBusy(false); }
  };
  return <button className={"stop " + className} disabled={busy || !ids.length} onClick={go} title="Queued runs are removed; running ones stop after the current step and keep their report">■ {label}</button>;
}

const STATUS_ICON = { passed: "ok", failed: "fail", cancelled: "minus", interrupted: "minus", queued: "clock", dispatched: "clock", running: "clock" };
export function Status({ s }) {
  return <span className={"status " + s}>{STATUS_ICON[s] ? <Icon name={STATUS_ICON[s]} size={13} /> : <i />}{s}</span>;
}

function Trend({ trend }) {
  const max = Math.max(1, ...trend.map((d) => d.passed + d.failed));
  if (!trend.length) return <div className="mut">No runs in the last 14 days</div>;
  return (
    <div className="trend" role="img" aria-label="Runs per day, last 14 days">
      {trend.map((d) => (
        <div key={d.d} className="tcol" title={`${d.d}: ${d.passed} passed, ${d.failed} failed`}>
          <div className="tbar">
            <i className="f" style={{ height: `${(100 * d.failed) / max}%` }} />
            <i className="p" style={{ height: `${(100 * d.passed) / max}%` }} />
          </div>
          <span>{d.d.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}

/** Failures worth a look: the newest run of each test, when it failed. */
function needsAttention(runs) {
  const seen = new Set(), out = [];
  for (const r of runs) {
    if (isLive(r) || seen.has(r.test_name)) continue;
    seen.add(r.test_name);
    if (r.status === "failed" || r.status === "interrupted") out.push(r);
  }
  return out;
}

export default function Dashboard() {
  const { runs, stats, nav, openRun, me, can, project } = useApp();
  const [stories, setStories] = useState(null);
  useEffect(() => {
    let off = false;
    api("/api/designs").then(async (list) => {
      const top = await Promise.all(list.slice(0, 5).map((d) => api(`/api/designs/${d.id}`).catch(() => null)));
      if (!off) setStories(top.filter(Boolean));
    }).catch(() => !off && setStories([]));
    return () => { off = true; };
  }, [project, runs.length]);
  const [flaky, setFlaky] = useState([]);
  useEffect(() => { api("/api/flaky?only=true").then(setFlaky).catch(() => setFlaky([])); }, [project, runs.length]);
  const live = runs.filter(isLive);
  const bad = needsAttention(runs).slice(0, 6);
  const finished = (stats?.passed || 0) + (stats?.failed || 0);
  const rate = finished ? Math.round((100 * stats.passed) / finished) : 0;
  const recent = runs.filter((r) => !isLive(r)).slice(0, 8);
  const mine = (r) => can.admin || !r.owner || r.owner === me?.username;

  return (
    <section className="view on">
      <div className="kpis">
        <div className="kpi"><div className="l">Pass rate</div><div className="v" style={{ color: finished ? (rate >= 80 ? "var(--ok)" : rate >= 50 ? "var(--warn)" : "var(--bad)") : undefined }}>{finished ? `${rate}%` : "–"}</div><div className="s">{stats?.passed || 0} of {finished} finished runs</div></div>
        <div className="kpi"><div className="l">Runs, last 24 h</div><div className="v">{stats?.day || 0}</div><div className="s">{stats?.day_failed ? <span style={{ color: "var(--bad)", fontWeight: 600 }}>{stats.day_failed} failed</span> : "none failed"}</div></div>
        <div className="kpi"><div className="l">Average duration</div><div className="v">{stats?.avg_ms ? secs(stats.avg_ms) : "–"}</div><div className="s">per finished run</div></div>
        <div className="kpi"><div className="l">Self-healed steps</div><div className="v">{stats?.healed || 0}</div><div className="s">locators repaired automatically</div></div>
      </div>

      <div className="dash-cols">
        <div className="dash-main">
          <section className="panel">
            <div className="panel-h"><h3>Needs attention</h3>{bad.length > 0 && <span className="status failed">{bad.length}</span>}<span className="spacer" />
              <button className="btn ghost sm" onClick={() => nav("runs")}>All runs</button></div>
            {bad.length ? bad.map((r) => (
              <div className="att" key={r.run_id}>
                <span style={{ color: "var(--bad)", paddingTop: 2 }}><Icon name="fail" size={18} /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <a href={`#/runs/${encodeURIComponent(r.run_id)}`} className="att-t">{r.test_name}</a>
                  <div className="mut">{r.passed_steps}/{r.total} steps · {r.engine}{r.browser ? ` · ${r.browser}` : ""} · {r.agent} · {ago(r.started_at)}{r.owner ? ` · ${r.owner}` : ""}</div>
                  {r.error && <div className="att-err" title={r.error}>{r.error}</div>}
                </div>
                <div className="att-acts"><button className="btn sm" onClick={() => openRun(r.run_id)}>Open</button>
                  {can.run && mine(r) && <button className="btn sm" onClick={() => openRun(r.run_id, "replay")}>Replay</button>}</div>
              </div>)) : <div className="empty-state">Nothing failing. The newest run of every test passed.</div>}
          </section>
          {flaky.length > 0 && <section className="panel">
            <div className="panel-h"><h3>Flaky tests</h3><span className="status failed">{flaky.length}</span><span className="spacer" />
              <button className="btn ghost sm" onClick={() => nav("automation", "flaky")}>See all</button></div>
            {flaky.slice(0, 4).map((f) => (
              <div className="att" key={f.name}>
                <span style={{ color: "var(--warn)", paddingTop: 2 }}><Icon name="refresh" size={18} /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <a href={`#/runs/${encodeURIComponent(f.last_run)}`} className="att-t">{f.name}</a>
                  <div className="mut"><Dots history={f.history} /> passes {Math.round(100 * f.pass_rate)}% · result changed {f.flips} times in {f.runs} runs</div>
                </div>
              </div>))}
          </section>}

          <section className="panel">
            <div className="panel-h"><h3>Recent runs</h3><span className="spacer" /><button className="btn ghost sm" onClick={() => nav("runs")}>All runs</button></div>
            <div style={{ overflowX: "auto" }}><table className="table compact">
              <thead><tr><th>Run</th><th>Result</th><th>Steps</th><th>Engine · machine</th><th>Duration</th><th>When</th></tr></thead>
              <tbody>{recent.map((r) => (
                <tr key={r.run_id} className="clickable" onClick={() => openRun(r.run_id)}>
                  <td><a href={`#/runs/${encodeURIComponent(r.run_id)}`} onClick={(e) => e.stopPropagation()} style={{ color: "var(--fg)", fontWeight: 550 }}>{r.test_name}</a></td>
                  <td><Status s={r.status} /></td>
                  <td style={{ minWidth: 110 }}><StepBar r={r} /></td>
                  <td className="nowrap mut">{r.engine}{r.browser ? `/${r.browser}` : ""} · {r.agent}</td>
                  <td className="mono nowrap">{secs(r.duration_ms)}</td><td className="mut nowrap">{ago(r.started_at)}</td>
                </tr>))}
                {!recent.length && <tr><td colSpan={6}><div className="empty-state">No runs yet. <a href="#/studio">Start a test</a></div></td></tr>}</tbody>
            </table></div>
          </section>
        </div>

        <div className="dash-side">
          <section className="panel">
            <div className="panel-h"><h3>Running now</h3><span className="spacer" />{live.length > 1 && can.run && <StopButton ids={live.map((r) => r.run_id)} label="Stop all" all className="btn sm" />}</div>
            {live.length ? live.map((r) => (
              <div className="att" key={r.run_id}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row"><Status s={r.status} /><b className="ellipsis">{r.test_name}</b></div>
                  <div className="mut ellipsis" style={{ margin: "4px 0 6px" }}>{r.agent} · {r.current_step ? `▶ ${r.current_step}` : "starting…"}</div>
                  <StepBar r={r} />
                </div>
                <div className="att-acts"><button className="btn sm" onClick={() => nav("studio", r.run_id)}>Watch</button>{can.run && mine(r) && <StopButton ids={[r.run_id]} className="btn sm" />}</div>
              </div>)) : <div className="empty-state">Nothing running. <a href="#/studio">Start a test</a> or <a href="#/requirements">run a test design</a>.</div>}
          </section>

          <section className="panel">
            <div className="panel-h"><h3>Stories</h3><span className="spacer" /><button className="btn ghost sm" onClick={() => nav("requirements")}>Test design</button></div>
            {stories === null ? <div style={{ padding: 16 }}><div className="skel" style={{ height: 14 }} /><div className="skel" style={{ height: 14, marginTop: 10, width: "70%" }} /></div>
              : stories.length ? stories.map((d) => {
                const approved = d.cases.filter((c) => c.status === "approved");
                const latest = approved.map((c) => d.latest?.[c.id]?.status).filter(Boolean);
                const ok = latest.filter((x) => x === "passed").length, ko = latest.filter((x) => x === "failed").length;
                return <a key={d.id} className="story-row" href="#/requirements" onClick={(e) => { e.preventDefault(); nav("requirements"); }}>
                  <div className="row"><span className="pill mono">{d.issue_key}</span><b className="ellipsis" style={{ flex: 1 }}>{d.issue_title}</b>
                    <span className="mut nowrap">{latest.length ? `${ok}/${approved.length}` : `${d.cases.length} cases`}</span></div>
                  {latest.length > 0 && <div className="sbar" style={{ marginTop: 8 }}><i className="p" style={{ width: `${(100 * ok) / approved.length}%` }} /><i className="f" style={{ width: `${(100 * ko) / approved.length}%` }} /></div>}
                  <div className="mut" style={{ marginTop: 6 }}>{approved.length} approved{latest.length < approved.length ? ` · ${approved.length - latest.length} not run yet` : ""}{d.unpublished ? ` · ${d.unpublished} result(s) not published` : ""}</div>
                </a>; })
              : <div className="empty-state">No test designs yet. <a href="#/requirements">Pick or paste a story</a> to get positive and negative cases.</div>}
          </section>

          <section className="panel">
            <div className="panel-h"><h3>Last 14 days</h3></div>
            <div className="panel-b" style={{ paddingTop: 4 }}><Trend trend={stats?.trend || []} /></div>
          </section>
        </div>
      </div>
    </section>
  );
}
