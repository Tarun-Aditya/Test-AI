import { useState } from "react";
import { ago, art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

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

export function Status({ s }) {
  return <span className={"status " + s}><i />{s}</span>;
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

export default function Dashboard() {
  const { runs, stats, nav, openRun } = useApp();
  const [filter, setFilter] = useState("");
  const live = runs.filter(isLive);
  const done = runs.filter((r) => !isLive(r));
  const finished = (stats?.passed || 0) + (stats?.failed || 0);
  const rate = finished ? Math.round((100 * stats.passed) / finished) : 0;
  const recent = done.filter((r) => !filter || r.status === filter).slice(0, 30);

  const kpis = [
    ["Pass rate", finished ? `${rate}%` : "–", `${stats?.passed || 0} of ${finished} finished runs`, finished ? (rate >= 80 ? "var(--ok)" : rate >= 50 ? "var(--warn)" : "var(--bad)") : ""],
    ["Running now", live.length, live.length ? [...new Set(live.map((r) => r.agent))].join(", ") : "idle", live.length ? "var(--warn)" : ""],
    ["Runs, last 24 h", stats?.day || 0, `${stats?.day_failed || 0} failed`, ""],
    ["Avg duration", stats?.avg_ms ? secs(stats.avg_ms) : "–", "per finished run", ""],
    ["Self-healed steps", stats?.healed || 0, "locators repaired automatically", stats?.healed ? "var(--warn)" : ""],
  ];

  return (
    <section className="view on">
      <div className="dash-top">
        <div className="kpis">
          {kpis.map(([l, v, s, c]) => (
            <div className="kpi" key={l}><div className="l">{l}</div><div className="v" style={c ? { color: c } : undefined}>{v}</div><div className="s">{s}</div></div>
          ))}
        </div>
        <div className="kpi trend-card"><div className="l">Last 14 days</div><Trend trend={stats?.trend || []} /></div>
      </div>

      <div className="sec-h"><h3>Running now</h3><span className="mut">{live.length ? `${live.length} in progress` : ""}</span></div>
      <div className="panel"><div className="panel-b" style={{ paddingTop: 4, paddingBottom: 4 }}>
        {live.length ? live.map((r) => (
          <div className="running-row" key={r.run_id}>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}><Status s={r.status} /><ModeBadge mode={r.mode} parser={r.parser} /><b className="ellipsis">{r.test_name}</b></div>
              <div className="mut ellipsis" style={{ marginTop: 4 }}>{r.engine}{r.browser ? ` · ${r.browser}` : ""} · {r.agent} · {r.current_step ? `▶ ${r.current_step}` : "starting…"}</div>
            </div>
            <div><StepBar r={r} /><div className="mut" style={{ marginTop: 4 }}>{r.done || 0} / {r.total} steps{r.failed_steps ? <span style={{ color: "var(--bad)" }}> · {r.failed_steps} failed</span> : null}</div></div>
            <div className="links"><button onClick={() => nav("studio", r.run_id)}><span className="dot-live" /> Watch live</button></div>
          </div>
        )) : <div className="empty-state" style={{ padding: 18 }}>Nothing running. <a href="#/studio">Start a test</a></div>}
      </div></div>

      <div className="sec-h"><h3>Recent runs</h3><span className="mut">{done.length} total, kept across restarts</span><span className="spacer" />
        <div className="seg">{[["", "All"], ["passed", "Passed"], ["failed", "Failed"], ["interrupted", "Interrupted"]].map(([k, l]) =>
          <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{l}</button>)}</div>
        <button className="btn sm" onClick={() => nav("runs")}>Table view</button></div>
      <div className="legend">
        <span>How each run's steps were made:</span>
        <span><ModeBadge mode="ai" parser="ai" /> Claude (AI) turned your English into steps</span>
        <span><ModeBadge mode="ai" parser="rules" /> built-in rule parser, used when AI is off</span>
        <span><ModeBadge mode="ai" parser="user" /> you edited the steps</span>
        <span><ModeBadge mode="replay" /> replay of a generated script, no parsing</span>
      </div>
      <div className="runcards">
        {recent.map((r) => (
          <div className="rc" key={r.run_id} onClick={() => openRun(r.run_id)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openRun(r.run_id)}>
            <div className="th">
              {r.thumb && <img loading="lazy" src={art(r.run_id, r.thumb)} alt="" onError={(e) => (e.currentTarget.style.display = "none")} />}
              <Status s={r.status} />
              <span className="mode-pos"><ModeBadge mode={r.mode} parser={r.parser} /></span>
              {r.video && <span className="vid"><Icon name="video" size={13} /></span>}
              <span className="dur">{secs(r.duration_ms)}</span>
            </div>
            <div className="bd">
              <div className="nm" title={r.test_name}>{r.test_name}</div>
              <StepBar r={r} />
              <div className="mut">{r.passed_steps}/{r.total} steps · {r.engine}{r.browser ? `/${r.browser}` : ""} · {r.agent} · {ago(r.started_at)}</div>
              {r.error && <div className="mut ellipsis" style={{ color: "var(--bad)" }} title={r.error}>{r.error}</div>}
            </div>
            <div className="acts links" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => openRun(r.run_id)}>Details</button>
              <a href={art(r.run_id, "report.html")} target="_blank" rel="noreferrer">Report</a>
              {r.video && <button onClick={() => openRun(r.run_id, "video")}>▶ Video</button>}
              <button onClick={() => openRun(r.run_id, "code")}>Code</button>
              <button className="accent" onClick={() => openRun(r.run_id, "replay")}>▶ Replay</button>
            </div>
          </div>
        ))}
        {!recent.length && <div className="card"><h4>No runs yet</h4><div className="mut">Finished runs appear here with a thumbnail, step results, report and video.</div></div>}
      </div>
    </section>
  );
}
