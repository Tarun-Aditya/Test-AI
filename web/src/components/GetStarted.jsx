import { useEffect, useState } from "react";
import { api, store } from "../api.js";
import { useApp } from "../App.jsx";
import { EXAMPLES } from "../form.js";
import Icon from "../icons.jsx";

/** A short checklist for someone new: write → run → keep → automate → get told. Hides itself when done or dismissed. */
export default function GetStarted() {
  const { runs, library, form, setForm, nav, can, project } = useApp();
  const [gone, setGone] = useState(() => store.get("getstarted_off", false));
  const [sched, setSched] = useState(0);
  const [chan, setChan] = useState(0);
  useEffect(() => {
    if (gone) return;
    api("/api/schedules").then((r) => setSched(r.length)).catch(() => {});
    api("/api/notifications").then((r) => setChan(r.length)).catch(() => {});
  }, [gone, project, runs.length, library.length]);
  if (gone || !can.run) return null;
  const steps = [
    ["Write a test", "Describe it in plain English, or record it with the Chrome extension.", !!form.text.trim() || runs.length > 0 || library.length > 0,
      <button className="btn sm primary" onClick={() => { const [, t] = Object.entries(EXAMPLES)[0]; if (!form.text.trim()) setForm({ text: t, srcMode: "text" }); nav("studio"); }}>Try an example</button>],
    ["Run it", "Watch every step happen, with a screenshot of each.", runs.length > 0, <button className="btn sm" onClick={() => nav("studio")}>Open Studio</button>],
    ["Keep the script", "Save a passing run to the Library as a script that runs without AI.", library.length > 0, <button className="btn sm" onClick={() => nav("runs")}>Open a run</button>],
    ["Run it automatically", "Every night, or from your CI after a deploy.", sched > 0, <button className="btn sm" onClick={() => nav("automation", "schedules")}>Schedule it</button>],
    ["Get told when it breaks", "A Slack, Teams, webhook or email message on failure.", chan > 0, <button className="btn sm" onClick={() => nav("automation", "alerts")}>Add an alert</button>],
  ];
  const done = steps.filter((s) => s[2]).length;
  if (done === steps.length) return null;
  return (
    <section className="panel getstarted" aria-label="Getting started">
      <div className="panel-h"><h3>Getting started</h3><span className="pill">{done} of {steps.length}</span><span className="spacer" />
        <button className="btn ghost sm" onClick={() => { store.set("getstarted_off", true); setGone(true); }}>Hide</button></div>
      <ol className="gs">
        {steps.map(([t, d, ok, act], i) => (
          <li key={t} className={ok ? "done" : ""}>
            <span className="gsn" aria-hidden="true">{ok ? <Icon name="ok" size={18} /> : i + 1}</span>
            <div><b>{t}</b><span className="mut">{d}</span></div>
            <span className="gsa">{ok ? <span className="mut">Done</span> : act}</span>
          </li>))}
      </ol>
    </section>
  );
}
