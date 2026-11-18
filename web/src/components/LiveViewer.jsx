import { useEffect, useRef, useState } from "react";
import { api, art, isLive, secs } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";
import { Prov, Status, StopButton } from "../views/Dashboard.jsx";

const ICON = { passed: "✓", failed: "✕", skipped: "–" };

/** Live (or recorded) view of one run: streamed browser frames, per-step screenshots and a step timeline. */
export default function LiveViewer({ runId }) {
  const { subscribe, openRun, nav } = useApp();
  const [run, setRun] = useState(null);
  const [steps, setSteps] = useState({});
  const [frame, setFrame] = useState(null);
  const [selected, setSelected] = useState(null);
  const [caption, setCaption] = useState("");
  const [url, setUrl] = useState("about:blank");
  const listRef = useRef(null);

  useEffect(() => {
    setRun(null); setSteps({}); setFrame(null); setSelected(null); setCaption(""); setUrl("about:blank");
    if (!runId) return;
    let alive = true;
    api(`/api/runs/${encodeURIComponent(runId)}`).then((r) => {
      if (!alive) return;
      setRun(r);
      const m = {};
      (r.steps || []).forEach((s) => (m[s.idx] = { ...s, img: s.screenshot ? art(runId, s.screenshot) : null }));
      setSteps(m);
      const last = [...(r.steps || [])].reverse().find((s) => s.url);
      if (last) setUrl(last.url);
    }).catch(() => setRun({ run_id: runId, status: "unknown", missing: true }));
    return () => { alive = false; };
  }, [runId]);

  useEffect(() => subscribe((e) => {
    if (e.run_id !== runId) return;
    const put = (i, patch) => setSteps((s) => ({ ...s, [i]: { ...(s[i] || { idx: i }), ...patch } }));
    switch (e.type) {
      case "run_started": setRun((r) => ({ ...(r || {}), status: "running", total: e.total, test_name: e.test, engine: e.engine, agent: e.agent || r?.agent })); break;
      case "live_frame": setFrame(`data:${e.mime};base64,${e.image}`); break;
      case "step_started": put(e.index, { status: "running", raw: e.raw, action: e.action, value: e.value, error: null, origin: e.origin, parsed_by: e.parsed_by }); setCaption(`Step ${e.index + 1} · ${e.raw}`); break;
      case "step_finished":
        put(e.index, { status: e.status, raw: e.raw, locator: e.locator, error: e.error, healed: e.healed, url: e.url, duration_ms: e.duration_ms, dialog: e.dialog,
          picked_by: e.picked_by, parsed_by: e.parsed_by, origin: e.origin });
        if (e.url) setUrl(e.url);
        break;
      case "step_shot": put(e.index, { img: `data:${e.mime};base64,${e.image}` }); break;
      case "healing": setCaption(`↻ Self-healing “${e.target}”: ${e.how || ""}`); break;
      case "paused": setCaption(`⏸ ${e.message} (${e.seconds}s)`); break;
      case "run_finished": setRun((r) => ({ ...(r || {}), status: e.status, healed_steps: e.healed })); setCaption(""); setFrame(null);
        if (e.status === "cancelled") setSteps((all) => Object.fromEntries(Object.entries(all).map(([k, st]) => [k, st.status === "running" ? { ...st, status: "skipped", error: "Cancelled by user" } : st])));
        api(`/api/runs/${encodeURIComponent(runId)}`).then((r) => setRun(r)).catch(() => {}); break;
      default:
    }
  }), [subscribe, runId]);

  const list = Object.values(steps).sort((a, b) => a.idx - b.idx);
  useEffect(() => { const el = listRef.current?.querySelector(".tstep.running"); el?.scrollIntoView({ block: "nearest" }); }, [list.length, list.find((s) => s.status === "running")?.idx]);

  const live = run && isLive(run);
  const total = run?.total || list.length;
  const done = list.filter((s) => ["passed", "failed", "skipped"].includes(s.status)).length;
  const passed = list.filter((s) => s.status === "passed").length;
  const failed = list.filter((s) => s.status === "failed").length;
  const healed = list.filter((s) => s.healed).length;
  const sel = selected !== null ? steps[selected] : null;
  const lastShot = [...list].reverse().find((s) => s.img)?.img;
  const image = sel?.img || (live ? frame || lastShot : lastShot);
  const pill = live ? (run.status === "running" ? "LIVE" : "QUEUED") : run ? "REPLAY" : "IDLE";

  return (
    <div className="sticky">
      <div className="browser">
        <div className="chrome">
          <div className="lights"><i /><i /><i /></div>
          <div className="urlbar"><Icon name="globe" size={13} /><span>{sel?.url || url}</span></div>
          <span className={"live-pill" + (live && run.status === "running" ? " on" : "")}><i />{pill}</span>
        </div>
        <div className="progress"><i style={{ width: total ? `${(100 * done) / total}%` : 0 }} /></div>
        <div className="screen">
          {image ? <>
            <img src={image} alt="Browser view" />
            {(sel || caption) && <div className="overlay"><span className="cap">{sel ? `Step ${sel.idx + 1} · ${sel.raw || ""}` : caption}</span></div>}
          </> : (
            <div className="empty">
              <div className="big"><Icon name={runId ? "pc" : "eye"} size={26} /></div>
              {runId ? (live ? <>Starting the browser on <b>{run?.agent}</b>…</> : run?.missing ? "Run not found." : "No screenshots were recorded for this run.")
                : <><b style={{ color: "var(--fg)" }}>Live browser view</b><br />Press <b>Run test</b>. The browser streams here from whichever machine runs it, with a screenshot for every step.</>}
            </div>
          )}
        </div>
        <div className="livebar">
          {run && !run.missing && <Status s={run.status} />}
          <span className="meta ellipsis">{run && !run.missing ? <><b style={{ color: "var(--fg)" }}>{run.test_name}</b> · {run.engine}{run.browser ? `/${run.browser}` : ""} · {run.agent}</> : "No run selected"}</span>
          <span className="spacer" />
          {total > 0 && <span className="stat-row"><span><b>{done}</b>/{total}</span><span style={{ color: "var(--ok)" }}><b style={{ color: "inherit" }}>{passed}</b> passed</span>
            {failed > 0 && <span style={{ color: "var(--bad)" }}><b style={{ color: "inherit" }}>{failed}</b> failed</span>}
            {healed > 0 && <span style={{ color: "var(--warn)" }}><b style={{ color: "inherit" }}>{healed}</b> healed</span>}</span>}
          {sel && <button className="btn sm" onClick={() => setSelected(null)}>{live ? "Follow live" : "Latest"}</button>}
          {live && <StopButton ids={[run.run_id]} label="Stop run" className="btn sm" />}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="panel-h"><h3>Steps</h3><span className="mut">{total ? `${done} of ${total}` : ""}</span><span className="spacer" />
          {run && !live && !run.missing && <span className="links">
            <button onClick={() => openRun(runId)}>Details</button>
            <a href={art(runId, "report.html")} target="_blank" rel="noreferrer">Report</a>
            {run.video && <button onClick={() => openRun(runId, "video")}>▶ Video</button>}
            <button onClick={() => nav("runs")}>All runs</button></span>}
        </div>
        <div className="timeline" ref={listRef}>
          {list.map((s) => (
            <div key={s.idx} className={`tstep ${s.status || ""} ${selected === s.idx ? "active" : ""}`} onClick={() => s.img && setSelected(selected === s.idx ? null : s.idx)}>
              <span className="ic">{s.status === "running" ? "" : ICON[s.status] || s.idx + 1}</span>
              <div style={{ minWidth: 0 }}>
                <div className="t1">{s.raw || s.action} <Prov parsed={s.parsed_by} picked={s.picked_by} /></div>
                {s.origin && s.origin !== s.raw && <div className="t2 origin">“{s.origin}”</div>}
                <div className="t2">{s.healed ? <span className="heal">self-healed · </span> : null}{s.locator || s.action}{s.duration_ms ? ` · ${secs(s.duration_ms)}` : ""}</div>
                {s.dialog && <div className="t2">dialog: {s.dialog}</div>}
                {s.error && <div className="err">{s.error}</div>}
              </div>
              {s.img ? <img src={s.img} alt={`Step ${s.idx + 1}`} onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="noimg" />}
            </div>
          ))}
          {!list.length && <div className="empty-state">Steps appear here as they run.</div>}
        </div>
      </div>
    </div>
  );
}
