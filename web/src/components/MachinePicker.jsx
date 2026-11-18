import { useEffect, useRef, useState } from "react";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

/** Machines a run goes to: the hub itself ("local"), any free machine, or several picked machines,
 *  either running everything on EACH of them or sharing the runs as a POOL. */
export function agentsOf(f) {
  const a = Array.isArray(f.agents) && f.agents.length ? f.agents : [f.agent || "local"];
  return a;
}

export function machineSummary(agents, spread, localName) {
  if (agents.includes("any")) return "Any free machine";
  const names = agents.map((a) => (a === "local" ? localName : a));
  if (names.length === 1) return names[0];
  return `${names.length} machines · ${spread === "pool" ? "shared pool" : "each runs all"}`;
}

export default function MachinePicker({ agents, spread, onChange, compact }) {
  const { machines, health } = useApp();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const online = machines.filter((m) => m.online);
  const any = agents.includes("any");
  const toggle = (name) => {
    let next = any ? [] : agents.filter((a) => a !== name);
    if (!agents.includes(name) || any) next = [...next, name];
    onChange({ agents: next.length ? next : ["local"], spread });
  };
  const rows = [
    { name: "local", label: health.local_name, sub: "this server", slots: health.local_slots || 1, running: health.local_running || 0 },
    ...online.map((m) => ({ name: m.name, label: m.name, sub: [m.platform, (m.engines || []).join(" + ")].filter(Boolean).join(" · "),
      slots: m.slots || 1, running: m.running || 0, queued: m.queued || 0 })),
  ];
  const offline = agents.filter((a) => a !== "local" && a !== "any" && !online.some((m) => m.name === a));
  return (
    <div className={"mpick" + (compact ? " compact" : "")} ref={ref}>
      <button type="button" className="mpick-btn" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)}
        title="Machines that run the browsers">
        <Icon name="pc" size={16} /><span className="ellipsis">{machineSummary(agents, spread, health.local_name)}</span>
        {offline.length > 0 && <span className="pill" style={{ color: "var(--bad)" }}>{offline.length} offline</span>}
        <span className="caret">▾</span>
      </button>
      {open && (
        <div className="mpick-pop" role="dialog" aria-label="Choose machines">
          <label className={"mrow" + (any ? " on" : "")}>
            <input type="checkbox" checked={any} onChange={() => onChange({ agents: any ? ["local"] : ["any"], spread })} />
            <div><b>Any free machine</b><small>Each run goes to whichever connected machine has a free slot{online.length ? "" : " (none connected: runs here)"}</small></div>
          </label>
          <div className="mpick-sep">or pick machines</div>
          {rows.map((r) => {
            const on = !any && agents.includes(r.name);
            const free = Math.max(0, r.slots - r.running);
            return (
              <label key={r.name} className={"mrow" + (on ? " on" : "")}>
                <input type="checkbox" checked={on} onChange={() => toggle(r.name)} />
                <div className="grow"><b>{r.label}</b><small>{r.sub}</small></div>
                <span className={"slots " + (free ? "ok" : "busy")} title={`${r.running} running of ${r.slots} parallel slot${r.slots > 1 ? "s" : ""}`}>
                  {r.running}/{r.slots}{r.queued ? ` · ${r.queued} waiting` : ""}</span>
              </label>
            );
          })}
          {offline.map((n) => <div key={n} className="mrow off"><span className="mut">{n} is offline: its runs wait until it reconnects</span>
            <button className="x" onClick={() => toggle(n)} title="Remove"><Icon name="x" size={13} /></button></div>)}
          {!any && agents.length > 1 && (
            <div className="mpick-spread">
              <div className="seg">
                <button className={spread !== "pool" ? "on" : ""} onClick={() => onChange({ agents, spread: "each" })}>Run on each</button>
                <button className={spread === "pool" ? "on" : ""} onClick={() => onChange({ agents, spread: "pool" })}>Share as pool</button>
              </div>
              <small>{spread === "pool"
                ? "Iterations (and engines) are spread over these machines: each run starts on the first one with a free slot."
                : "Every iteration runs on every selected machine, all at the same time where slots allow."}</small>
            </div>
          )}
          <small className="mpick-foot">x/y = running / parallel slots. Start an agent with <code>--slots N</code> to run N tests at once on it.</small>
        </div>
      )}
    </div>
  );
}
