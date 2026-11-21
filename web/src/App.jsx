import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, currentProject, isLive, setProject, store } from "./api.js";
import { dataPayload, loadForm, runConfig, saveForm } from "./form.js";
import Icon from "./icons.jsx";
import Dashboard from "./views/Dashboard.jsx";
import Studio from "./views/Studio.jsx";
import Runs from "./views/Runs.jsx";
import Library from "./views/Library.jsx";
import Machines from "./views/Machines.jsx";
import Sessions from "./views/Sessions.jsx";
import Settings from "./views/Settings.jsx";
import MyAI from "./views/MyAI.jsx";
import Requirements from "./views/Requirements.jsx";
import Automation from "./views/Automation.jsx";
import MachinePicker, { agentsOf } from "./components/MachinePicker.jsx";
import RunPage from "./components/RunDrawer.jsx";

export const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

// view key -> [title, one-line description, icon]
const VIEWS = {
  dashboard: ["Overview", "What's running, what's failing, how each story is covered", "dash"],
  requirements: ["Test design", "Stories and bugs → test cases → runs → results back", "doc"],
  studio: ["Studio", "Describe a test, watch it run, keep the script", "studio"],
  runs: ["Runs", "Every run, grouped by launch, with its report, video and scripts", "runs"],
  library: ["Library", "Reusable scripts with native locators. Run them on any engine or machine", "lib"],
  sessions: ["Saved logins", "Cookies and local storage a run can start with", "key"],
  automation: ["Automation", "Schedules, CI hooks, alerts and flaky tests", "clock"],
  machines: ["Machines", "Computers and browser grids that run tests", "pc"],
  settings: ["Admin", "AI, integrations, sign-in, users and projects", "gear"],
  myai: ["My AI", "Your own AI subscription or key, used for your runs", "spark"],
};
// the sidebar, in workflow order: design → write → run → review, then things set up once (My AI lives in the user menu)
const NAV_GROUPS = [[null, ["dashboard", "requirements", "studio", "runs"]], ["Assets", ["library", "sessions"]], ["Automate", ["automation"]], ["Setup", ["machines", "settings"]]];
const PHONE_TABS = ["dashboard", "requirements", "studio", "runs"];
// which pages a role sees (no accounts = single user, sees everything except My AI)
const visibleViews = (me) => Object.keys(VIEWS).filter((k) => (k === "myai" ? !!me : k === "settings" ? !me || me.role === "admin" : true));
const THEMES = ["auto", "light", "dark"];

function useHashRoute() {
  const parse = () => {
    const [v, id, tab] = (location.hash.replace(/^#\/?/, "") || store.get("view", "dashboard")).split("/");
    return { view: VIEWS[v] ? v : "dashboard", id: id ? decodeURIComponent(id) : null, tab: tab || null };
  };
  const [route, setRoute] = useState(parse);
  useEffect(() => { const h = () => setRoute(parse()); window.addEventListener("hashchange", h); return () => window.removeEventListener("hashchange", h); }, []);
  return route;
}

export default function App({ me = null, reloadMe = () => {} }) {
  const route = useHashRoute();
  const role = me ? me.role : "admin";
  const can = { run: role !== "viewer", admin: role === "admin" };
  const [menu, setMenu] = useState(false);
  const [projects, setProjects] = useState([]);
  const [project, setProjectState] = useState(currentProject);
  const [form, setFormState] = useState(loadForm);
  const [runs, setRuns] = useState([]);
  const [stats, setStats] = useState(null);
  const [machines, setMachines] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [library, setLibrary] = useState([]);
  const [health, setHealth] = useState({ llm: "…", local_name: "local" });
  const [more, setMore] = useState(false);
  const [toastMsg, setToastMsg] = useState(null);
  const [theme, setTheme] = useState(() => store.get("theme", "auto"));
  const [busy, setBusy] = useState(false);
  const listeners = useRef(new Set());

  const setForm = useCallback((patch) => setFormState((f) => { const n = { ...f, ...(typeof patch === "function" ? patch(f) : patch) }; saveForm(n); return n; }), []);
  const toast = useCallback((msg, err) => { setToastMsg({ msg, err, t: Date.now() }); }, []);
  useEffect(() => { if (!toastMsg) return; const h = setTimeout(() => setToastMsg(null), 3800); return () => clearTimeout(h); }, [toastMsg]);
  const nav = useCallback((view, id, tab) => { location.hash = `/${view}${id ? "/" + encodeURIComponent(id) : ""}${id && tab ? "/" + tab : ""}`; if (!id) store.set("view", view); }, []);
  useEffect(() => { setMenu(false); setMore(false); window.scrollTo(0, 0); }, [route.view, route.id]);

  useEffect(() => { if (theme === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = theme; store.set("theme", theme); }, [theme]);

  const loadRuns = useCallback(async () => { const [r, s] = await Promise.all([api("/api/runs"), api("/api/stats")]); setRuns(r); setStats(s); }, []);
  const loadMachines = useCallback(async () => {
    const [m, h] = await Promise.all([api("/api/agents"), api("/api/health").catch(() => null)]);
    setMachines(m);
    if (h) setHealth(h);
  }, []);
  const loadSessions = useCallback(async () => setSessions(await api("/api/sessions")), []);
  const loadProjects = useCallback(async () => {
    const ps = await api("/api/projects").catch(() => []);
    setProjects(ps);
    if (ps.length && !ps.some((p) => p.key === currentProject())) { setProject("default"); setProjectState("default"); }
  }, []);
  useEffect(() => { loadProjects(); }, [loadProjects]);
  const switchProject = (key) => {
    setProject(key); setProjectState(key);
    loadRuns(); loadSessions(); loadLibrary();
    nav("dashboard");
  };
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
    if (e.type === "agents_changed" || e.type === "ai_changed") loadMachines();
    if (e.type === "session_saved") { loadSessions(); toast(`Session “${e.name}” saved`); }
  }), [subscribe, loadRuns, loadMachines, loadSessions, toast]);

  useEffect(() => {
    api("/api/health").then(setHealth).catch(() => {});
    loadRuns(); loadMachines(); loadSessions(); loadLibrary();
  }, [loadRuns, loadMachines, loadSessions, loadLibrary]);
  // keep progress fresh while something is running
  const anyLive = runs.some(isLive);
  useEffect(() => { if (!anyLive) return; const h = setInterval(() => { loadRuns(); loadMachines(); }, 2500); return () => clearInterval(h); }, [anyLive, loadRuns, loadMachines]);

  const startRun = useCallback(async (extra = {}) => {
    setBusy(true);
    try {
      const d = dataPayload(form);
      const agents = agentsOf(form);
      const body = { data: d.data, sensitive: d.sensitive, iterations: d.iterations || null, config: runConfig(form),
        agent: agents.length > 1 ? agents : agents[0], spread: form.spread || "each",
        engines: form.engine === "both" ? ["playwright", "selenium"] : null, ...extra };
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
      if (r.run_ids.length > 1) { nav("dashboard"); toast(`Started ${r.run_ids.length} runs in parallel. Open any of them to watch it live`); }
      else { nav("studio", r.run_ids[0]); toast("Run started"); }
      return r;
    } catch (e) { toast(e.message, true); } finally { setBusy(false); }
  }, [form, nav, toast, loadRuns]);

  const ctx = useMemo(() => ({
    form, setForm, runs, stats, machines, sessions, library, health, toast, nav, route, subscribe, startRun, busy,
    loadRuns, loadMachines, loadSessions, loadLibrary, openRun: (id, tab) => nav("runs", id, tab), me, can,
    projects, project, loadProjects,
  }), [form, setForm, runs, stats, machines, sessions, library, health, toast, nav, route, subscribe, startRun, busy, loadRuns, loadMachines, loadSessions, loadLibrary, me, projects, project, loadProjects]);
  const signOut = async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch { /* already out */ } reloadMe(); };
  const changePassword = async () => {
    const current = me?.has_password ? window.prompt("Current password") : "";
    if (current === null) return;
    const next = window.prompt("New password (at least 10 characters)");
    if (!next) return;
    try { await api("/api/me/password", { method: "POST", body: { current, new: next } }); toast("Password changed. Other devices were signed out"); }
    catch (e) { toast(e.message, true); }
  };

  const liveCount = runs.filter(isLive).length;
  const [title, sub] = VIEWS[route.view];
  const online = machines.filter((m) => m.online);
  const counts = { runs: runs.length, library: library.length, machines: online.length + 1, sessions: sessions.length };
  const views = visibleViews(me);
  const runPage = route.view === "runs" && route.id;
  const navItem = (k, phone) => (
    <a key={k} href={`#/${k}`} className={route.view === k ? "on" : ""} aria-current={route.view === k ? "page" : undefined}
      onClick={(e) => { e.preventDefault(); nav(k); }}>
      <Icon name={VIEWS[k][2]} /><span className="lbl">{VIEWS[k][0]}</span>
      {!phone && k === "dashboard" && liveCount > 0 && <span className="count live">{liveCount} live</span>}
      {!phone && counts[k] !== undefined && <span className="count">{counts[k]}</span>}
    </a>);

  return (
    <Ctx.Provider value={ctx}>
      <div className="app">
        <aside className="side">
          <div className="brand"><div className="logo"><Icon name="spark" /></div><div><b>Autopilot</b><small>AI test automation</small></div></div>
          {projects.length > 1 && <label className="projsel" title="Project: its runs, scripts and saved logins">
            <small>Project</small>
            <select value={project} onChange={(e) => switchProject(e.target.value)}>
              {projects.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}</select></label>}
          {can.run && <button className="btn primary newtest" onClick={() => nav("studio")}><Icon name="plus" size={15} />New test</button>}
          <nav className="nav" aria-label="Main">
            {NAV_GROUPS.map(([label, keys]) => {
              const ks = keys.filter((k) => views.includes(k));
              return ks.length ? <div key={label || "work"} className="navgroup">{label && <div className="navh">{label}</div>}{ks.map((k) => navItem(k))}</div> : null;
            })}
          </nav>
          <div className="side-foot">
            <button className="chip" title={health.llm} onClick={() => nav("settings")}><span className={"dotc " + (health.llm_on ? "ok" : "")} />AI {health.llm_on ? "on" : "off · built-in rules"}</button>
            {me ? <div className="usermenu">
              <button className="userchip" aria-haspopup="true" aria-expanded={menu} onClick={() => setMenu(!menu)}>
                <span className="avatar">{(me.name || me.username).slice(0, 1).toUpperCase()}</span>
                <span className="ellipsis"><b>{me.name || me.username}</b><small>{me.role}{me.sso ? " · SSO" : ""}</small></span></button>
              {menu && <div className="umenu" onMouseLeave={() => setMenu(false)}>
                <button onClick={() => nav("myai")}>My AI subscription</button>
                {me.has_password && <button onClick={() => { setMenu(false); changePassword(); }}>Change password</button>}
                <button onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % 3])}>Theme: {theme}</button>
                <button onClick={signOut}>Sign out</button>
              </div>}
            </div> : <button className="theme" onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % 3])}><Icon name="moon" size={14} />Theme: {theme}</button>}
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            {runPage ? <div className="crumbs"><a href="#/runs" onClick={(e) => { e.preventDefault(); nav("runs"); }}><Icon name="back" size={14} />Runs</a></div>
              : <div><h1>{title}</h1><div className="sub">{sub}</div></div>}
            <div className="spacer" />
            {route.view === "studio" && can.run && <div className="runbar" role="group" aria-label="Run settings">
              <div className="seg" title="Execution engine. Both: every run once with Playwright and once with Selenium, in parallel">
                {[["playwright", "Playwright"], ["selenium", "Selenium"], ["both", "Both"]].map(([e, l]) =>
                  <button key={e} className={form.engine === e ? "on" : ""} aria-pressed={form.engine === e} onClick={() => setForm({ engine: e })}>{l}</button>)}
              </div>
              <MachinePicker agents={agentsOf(form)} spread={form.spread || "each"}
                onChange={({ agents, spread }) => setForm({ agents, spread, agent: agents[0] })} />
              <label className="toggle"><input type="checkbox" checked={form.headless} onChange={(e) => setForm({ headless: e.target.checked })} /><span className="sw" />Headless</label>
              <button className="btn primary" disabled={busy} onClick={() => startRun()}><Icon name="play" size={14} />Run test</button>
            </div>}
            {!can.run && <span className="pill">viewer: read-only</span>}
          </header>

          {route.view === "dashboard" && <Dashboard />}
          {route.view === "studio" && <Studio runId={route.id} />}
          {route.view === "requirements" && <Requirements />}
          {route.view === "runs" && (runPage ? <RunPage key={route.id} id={route.id} tab={route.tab} /> : <Runs />)}
          {route.view === "library" && <Library />}
          {route.view === "machines" && <Machines />}
          {route.view === "sessions" && <Sessions />}
          {route.view === "automation" && <Automation />}
          {route.view === "settings" && (can.admin ? <Settings /> : <div className="view on"><div className="empty-state">Only admins can open this page.</div></div>)}
          {route.view === "myai" && me && <MyAI />}
        </div>

        <nav className="phonebar" aria-label="Main">
          {PHONE_TABS.filter((k) => views.includes(k)).map((k) => navItem(k, true))}
          <button className={more ? "on" : ""} aria-expanded={more} onClick={() => setMore(!more)}><Icon name="more" /><span className="lbl">More</span></button>
          {more && <div className="phonemore">
            {["library", "sessions", "automation", "machines", "settings", "myai"].filter((k) => views.includes(k)).map((k) => navItem(k, true))}
            <button onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % 3])}><Icon name="moon" /><span className="lbl">Theme: {theme}</span></button>
            {me && <button onClick={signOut}><Icon name="x" /><span className="lbl">Sign out</span></button>}
          </div>}
        </nav>
      </div>
      <div className={"toast" + (toastMsg ? " on" : "") + (toastMsg?.err ? " err" : "")} role="status">{toastMsg?.msg}</div>
    </Ctx.Provider>
  );
}
