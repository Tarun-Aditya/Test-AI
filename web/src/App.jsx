import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, isLive, store } from "./api.js";
import { dataPayload, loadForm, runConfig, saveForm } from "./form.js";
import Icon from "./icons.jsx";
import Dashboard from "./views/Dashboard.jsx";
import Studio from "./views/Studio.jsx";
import Runs from "./views/Runs.jsx";
import Library from "./views/Library.jsx";
import Machines from "./views/Machines.jsx";
import Sessions from "./views/Sessions.jsx";
import RunDrawer from "./components/RunDrawer.jsx";

export const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

const VIEWS = {
  dashboard: ["Dashboard", "Recent and running tests at a glance", "dash"],
  studio: ["Studio", "Describe a test, watch it run, keep the script", "studio"],
  runs: ["Runs", "Every execution with its report, video and generated scripts", "runs"],
  library: ["Script library", "Reusable scripts with native locators. Run them on any engine or machine", "lib"],
  machines: ["Machines", "Computers that can run tests", "pc"],
  sessions: ["Sessions", "Saved logins (cookies + localStorage) a run can start with", "key"],
};
const THEMES = ["auto", "light", "dark"];

function useHashRoute() {
  const parse = () => {
    const [v, id] = (location.hash.replace(/^#\/?/, "") || store.get("view", "dashboard")).split("/");
    return { view: VIEWS[v] ? v : "dashboard", id: id ? decodeURIComponent(id) : null };
  };
  const [route, setRoute] = useState(parse);
  useEffect(() => { const h = () => setRoute(parse()); window.addEventListener("hashchange", h); return () => window.removeEventListener("hashchange", h); }, []);
  return route;
}

export default function App() {
  const route = useHashRoute();
  const [form, setFormState] = useState(loadForm);
  const [runs, setRuns] = useState([]);
  const [stats, setStats] = useState(null);
  const [machines, setMachines] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [library, setLibrary] = useState([]);
  const [health, setHealth] = useState({ llm: "…", local_name: "local" });
  const [drawer, setDrawer] = useState(null);
  const [toastMsg, setToastMsg] = useState(null);
  const [theme, setTheme] = useState(() => store.get("theme", "auto"));
  const [busy, setBusy] = useState(false);
  const listeners = useRef(new Set());

  const setForm = useCallback((patch) => setFormState((f) => { const n = { ...f, ...(typeof patch === "function" ? patch(f) : patch) }; saveForm(n); return n; }), []);
  const toast = useCallback((msg, err) => { setToastMsg({ msg, err, t: Date.now() }); }, []);
  useEffect(() => { if (!toastMsg) return; const h = setTimeout(() => setToastMsg(null), 3800); return () => clearTimeout(h); }, [toastMsg]);
  const nav = useCallback((view, id) => { location.hash = `/${view}${id ? "/" + encodeURIComponent(id) : ""}`; store.set("view", view); }, []);

  useEffect(() => { if (theme === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = theme; store.set("theme", theme); }, [theme]);

  const loadRuns = useCallback(async () => { const [r, s] = await Promise.all([api("/api/runs"), api("/api/stats")]); setRuns(r); setStats(s); }, []);
  const loadMachines = useCallback(async () => setMachines(await api("/api/agents")), []);
  const loadSessions = useCallback(async () => setSessions(await api("/api/sessions")), []);
  const loadLibrary = useCallback(async () => setLibrary(await api("/api/library")), []);

  // live events from the hub (one socket for the whole app)
  useEffect(() => {
    let ws, stop = false, timer;
    const connect = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/ui`);
      ws.onmessage = (m) => { const e = JSON.parse(m.data); listeners.current.forEach((fn) => fn(e)); };
      ws.onclose = () => { if (!stop) timer = setTimeout(connect, 2000); };
    };
    connect();
    return () => { stop = true; clearTimeout(timer); ws && ws.close(); };
  }, []);
  const subscribe = useCallback((fn) => { listeners.current.add(fn); return () => listeners.current.delete(fn); }, []);

  useEffect(() => subscribe((e) => {
    if (["run_started", "run_finished", "runs_changed", "artifacts_uploaded"].includes(e.type)) loadRuns();
    if (e.type === "agents_changed") loadMachines();
    if (e.type === "session_saved") { loadSessions(); toast(`Session “${e.name}” saved`); }
  }), [subscribe, loadRuns, loadMachines, loadSessions, toast]);

  useEffect(() => {
    api("/api/health").then(setHealth).catch(() => {});
    loadRuns(); loadMachines(); loadSessions(); loadLibrary();
  }, [loadRuns, loadMachines, loadSessions, loadLibrary]);
  // keep progress fresh while something is running
  const anyLive = runs.some(isLive);
  useEffect(() => { if (!anyLive) return; const h = setInterval(loadRuns, 2500); return () => clearInterval(h); }, [anyLive, loadRuns]);

  const startRun = useCallback(async (extra = {}) => {
    setBusy(true);
    try {
      const d = dataPayload(form);
      const body = { data: d.data, sensitive: d.sensitive, iterations: d.iterations || null, config: runConfig(form), agent: form.agent, ...extra };
      if (!extra.library && !extra.replay_run_id) {
        if (form.srcMode === "lib") { if (!form.libName) throw new Error("Pick a script from the library"); body.library = form.libName; }
        else {
          if (!form.text.trim()) throw new Error("Describe the test first");
          body.text = form.text; body.name = (form.name || "").trim() || null;
          // run the reviewed / edited breakdown when it still matches the text
          if (form.steps && form.steps.length && form.stepsText === form.text) body.steps = form.steps;
        }
      }
      const r = await api("/api/runs", { method: "POST", body });
      loadRuns();
      if (r.run_ids.length > 1) { nav("dashboard"); toast(`Started ${r.run_ids.length} iterations. Open any of them to watch it live`); }
      else { nav("studio", r.run_ids[0]); toast("Run started"); }
      return r;
    } catch (e) { toast(e.message, true); } finally { setBusy(false); }
  }, [form, nav, toast, loadRuns]);

  const ctx = useMemo(() => ({
    form, setForm, runs, stats, machines, sessions, library, health, toast, nav, route, subscribe, startRun, busy,
    loadRuns, loadMachines, loadSessions, loadLibrary, openRun: (id, tab) => setDrawer({ id, tab: tab || "overview" }),
  }), [form, setForm, runs, stats, machines, sessions, library, health, toast, nav, route, subscribe, startRun, busy, loadRuns, loadMachines, loadSessions, loadLibrary]);

  const liveCount = runs.filter(isLive).length;
  const [title, sub] = VIEWS[route.view];
  const online = machines.filter((m) => m.online);
  const counts = { runs: runs.length, library: library.length, machines: online.length + 1, sessions: sessions.length };

  return (
    <Ctx.Provider value={ctx}>
      <div className="app">
        <aside className="side">
          <div className="brand"><div className="logo"><Icon name="spark" /></div><div><b>Autopilot</b><small>AI test automation</small></div></div>
          <nav className="nav">
            {Object.entries(VIEWS).map(([k, [label, , icon]]) => (
              <button key={k} className={route.view === k ? "on" : ""} onClick={() => nav(k)}>
                <Icon name={icon} /><span className="lbl">{label === "Script library" ? "Library" : label}</span>
                {k === "dashboard" && liveCount > 0 && <span className="count" style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>{liveCount} live</span>}
                {counts[k] !== undefined && <span className="count">{counts[k]}</span>}
              </button>
            ))}
          </nav>
          <div className="side-foot">
            <span className="chip" title={health.llm}><span className={"dotc " + (health.llm_on ? "ok" : "")} />AI {health.llm_on ? "on" : "off (rules)"}</span>
            <button className="theme" onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % 3])}><Icon name="moon" size={14} />Theme: {theme}</button>
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            <div><h1>{title}</h1><div className="sub">{sub}</div></div>
            <div className="spacer" />
            <div className="seg" title="Execution engine">
              {["playwright", "selenium"].map((e) => <button key={e} className={form.engine === e ? "on" : ""} onClick={() => setForm({ engine: e })}>{e === "playwright" ? "Playwright" : "Selenium"}</button>)}
            </div>
            <label className="select" title="Machine that runs the browser"><Icon name="pc" size={16} />
              <select value={form.agent} onChange={(e) => setForm({ agent: e.target.value })}>
                <option value="local">{health.local_name} (this server)</option>
                {online.length > 0 && <option value="any">Any free machine</option>}
                {online.map((m) => <option key={m.name} value={m.name}>{m.name}{m.busy ? " · busy" : ""}</option>)}
                {form.agent !== "local" && form.agent !== "any" && !online.some((m) => m.name === form.agent) && <option value={form.agent}>{form.agent} (offline)</option>}
              </select></label>
            <label className="toggle"><input type="checkbox" checked={form.headless} onChange={(e) => setForm({ headless: e.target.checked })} /><span className="sw" />Headless</label>
            <button className="btn primary" disabled={busy} onClick={() => startRun()}><Icon name="play" size={14} />Run test</button>
          </header>

          {route.view === "dashboard" && <Dashboard />}
          {route.view === "studio" && <Studio runId={route.id} />}
          {route.view === "runs" && <Runs />}
          {route.view === "library" && <Library />}
          {route.view === "machines" && <Machines />}
          {route.view === "sessions" && <Sessions />}
        </div>
      </div>
      {drawer && <RunDrawer {...drawer} onClose={() => setDrawer(null)} />}
      <div className={"toast" + (toastMsg ? " on" : "") + (toastMsg?.err ? " err" : "")} role="status">{toastMsg?.msg}</div>
    </Ctx.Provider>
  );
}
