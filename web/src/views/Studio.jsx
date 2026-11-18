import { useRef, useState } from "react";
import { ago, api } from "../api.js";
import { Status } from "./Dashboard.jsx";
import { useApp } from "../App.jsx";
import DataEditor from "../components/DataEditor.jsx";
import LiveViewer from "../components/LiveViewer.jsx";
import { BROWSERS, EXAMPLES, dataPayload, previewValues } from "../form.js";
import Icon from "../icons.jsx";

function Toggle({ k, label }) {
  const { form, setForm } = useApp();
  return <label className="toggle"><input type="checkbox" checked={!!form[k]} onChange={(e) => setForm({ [k]: e.target.checked })} /><span className="sw" />{label}</label>;
}
function Field({ label, hint, children }) {
  return <div className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</div>;
}

const ACTIONS = ["navigate", "click", "double_click", "right_click", "type", "append", "press", "hover", "select", "check", "uncheck",
  "drag", "scroll", "wait", "pause", "upload", "assert_text", "assert_visible", "assert_url", "assert_title", "assert_dialog", "dialog",
  "switch_tab", "back", "refresh", "screenshot"];

/** The AI (or rule) breakdown of the English, editable before running. */
function Breakdown() {
  const { form, setForm, health } = useApp();
  const steps = form.steps || [];
  const upd = (i, patch) => setForm((f) => ({ steps: f.steps.map((s, j) => (j === i ? { ...s, ...patch, source: "user", locator: null, fallbacks: [] } : s)) }));
  const stale = form.stepsText !== form.text;
  const fill = previewValues(form);
  return (
    <div className="breakdown">
      <div className="bd-h">
        <b>{steps.length} steps</b>
        <span className="mut">split by {form.stepsParser === "ai" ? <span className="prov ai">AI (Claude)</span> : <span className="prov">rule parser</span>}
          {stale && <span style={{ color: "var(--warn)" }}> · text changed since. Break down again to refresh</span>}</span>
        <span className="spacer" />
        <button className="btn sm ghost" onClick={() => setForm({ steps: null, stepsText: null })}>Discard</button>
      </div>
      <div className="bd-list">
        {steps.map((s, i) => (
          <div className="bd-row" key={i}>
            <span className="n">{i + 1}</span>
            <select value={s.action} onChange={(e) => upd(i, { action: e.target.value })}>{ACTIONS.map((a) => <option key={a}>{a}</option>)}</select>
            <input value={s.target || ""} placeholder="element (in words)" onChange={(e) => upd(i, { target: e.target.value || null })} />
            <input value={s.value || ""} placeholder="value" onChange={(e) => upd(i, { value: e.target.value || null })} />
            <span className="bd-meta">
              {s.nth != null && <span className="pill">#{s.nth < 0 ? "last" : s.nth + 1}</span>}
              {s.optional && <span className="pill">optional</span>}
              <span className={"prov " + (s.source === "ai" ? "ai" : "")}>{s.source === "ai" ? "AI" : s.source === "user" ? "edited" : "rules"}</span>
            </span>
            <button className="x" title="Remove step" onClick={() => setForm((f) => ({ steps: f.steps.filter((_, j) => j !== i) }))}><Icon name="x" size={14} /></button>
            {(fill(s.target) || fill(s.value)) && <div className="bd-fill">with your data: {[fill(s.target), fill(s.value)].filter(Boolean).join(" · ")}</div>}
            {s.origin && <div className="bd-origin">from your text: “{s.origin}”</div>}
          </div>
        ))}
      </div>
      <button className="btn sm" onClick={() => setForm((f) => ({ steps: [...(f.steps || []), { action: "click", target: "", value: null, source: "user", raw: "" }] }))}>
        <Icon name="plus" size={14} />Add step</button>
      <p className="hint">These steps run when you press <b>Run test</b>. Elements are identified on the live page{health.llm_on ? ", with AI breaking ties" : ""}.</p>
    </div>
  );
}

function Script() {
  const { form, setForm, library, toast, loadLibrary, health } = useApp();
  const [busy, setBusy] = useState(false);
  const [unparsed, setUnparsed] = useState([]);
  const gutter = useRef(null);
  const lines = Math.max(1, form.text.split("\n").length);
  const breakDown = async () => {
    setBusy(true);
    try {
      const d = dataPayload(form);
      const data = d.iterations ? { ...(d.data || {}), ...d.iterations[0].data } : d.data;
      const r = await api("/api/parse", { method: "POST", body: { text: form.text, data, sensitive: d.sensitive, use_llm: form.useLlm } });
      setUnparsed(r.unparsed);
      setForm({ steps: r.test.steps, stepsText: form.text, stepsParser: r.parser.startsWith("llm") ? "ai" : "rules" });
    } catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const upload = async (file) => {
    const fd = new FormData(); fd.append("file", file);
    try { const r = await api("/api/library/upload", { method: "POST", body: fd }); await loadLibrary(); setForm({ libName: r.name }); toast(`Uploaded “${r.name}” (${r.steps} steps)`); }
    catch (e) { toast(e.message, true); }
  };
  return (
    <>
      <div className="seg" style={{ marginBottom: 12 }}>
        <button className={form.srcMode === "text" ? "on" : ""} onClick={() => setForm({ srcMode: "text" })}>Describe in English</button>
        <button className={form.srcMode === "lib" ? "on" : ""} onClick={() => setForm({ srcMode: "lib" })}>From library</button>
      </div>
      {form.srcMode === "text" ? <>
        {!health.llm_on && <div className="note"><Icon name="spark" size={14} /><span>AI is off, so the built-in rules read your steps (one action per line works best).
          Free-form wording needs AI: <a href="#/settings">set it up in Admin</a>.</span></div>}
        <input className="name-in" value={form.name || ""} placeholder="Test name (optional; leave empty to name it from what the test does)" onChange={(e) => setForm({ name: e.target.value })} aria-label="Test name" />
        <div className="editor">
          <div className="gutter" ref={gutter}>{Array.from({ length: lines }, (_, i) => i + 1).join("\n")}</div>
          <textarea spellCheck={false} value={form.text} onChange={(e) => setForm({ text: e.target.value })} style={{ whiteSpace: "pre-wrap" }}
            onScroll={(e) => gutter.current && (gutter.current.scrollTop = e.currentTarget.scrollTop)}
            placeholder={"Write your test here, e.g.\nOpen ${base_url}/login\nType ${user.email} into Email\nClick Sign in\nVerify page contains \"Welcome\""} aria-label="Test steps" />
        </div>
        <p className="hint" style={{ margin: "8px 0 0" }}><code>{"${name}"}</code> is a placeholder: it is replaced by the value called <i>name</i> under
          <b> Test data</b> (e.g. <code>{"${brand}"}</code> → Logitech), so the same test can run with different data.</p>
        <div className="examples">
          <span className="mut">Examples:</span>
          {Object.keys(EXAMPLES).map((k) => <button key={k} onClick={() => setForm({ text: EXAMPLES[k], steps: null })}>{k}</button>)}
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12 }}>
          <button className="btn primary" disabled={busy || !form.text.trim()} onClick={breakDown}><Icon name="spark" size={14} />{busy ? "Breaking down…" : "Break down into steps"}</button>
          <span className="mut">{health.llm_on ? "Claude splits your description into steps" : "The rule parser splits your description into steps"}. Review them, then Run test.</span>
        </div>
        {unparsed.length > 0 && <div className="callout" style={{ background: "var(--bad-soft)", color: "var(--bad)" }}>Not understood: {unparsed.join(" · ")}</div>}
        {form.steps && form.steps.length > 0 && <Breakdown />}
      </> : <>
        <div className="field"><span>Saved script</span>
          <select value={form.libName} onChange={(e) => setForm({ libName: e.target.value })}>
            <option value="">Choose a script…</option>
            {library.map((l) => <option key={l.name} value={l.name}>{l.name} · {l.steps} steps</option>)}
          </select><small>Runs with the stored native locators (Script mode). Steps re-identify their element (self-heal) if the page changed.</small></div>
        <label className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); }}>
          <Icon name="up" /><b>Upload a script</b><span className="mut">script.json, or a generated test_playwright.py / test_selenium.py</span>
          <input type="file" accept=".json,.py" onChange={(e) => { e.target.files[0] && upload(e.target.files[0]); e.target.value = ""; }} />
        </label>
      </>}
    </>
  );
}

function Options() {
  const { form, setForm } = useApp();
  return (
    <>
      <div className="grid2">
        <Field label="Browser" hint="Iterations can choose their own browser">
          <select value={form.browser} onChange={(e) => setForm({ browser: e.target.value })}>{BROWSERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        <Field label="Step timeout">
          <select value={form.timeout} onChange={(e) => setForm({ timeout: +e.target.value })}>{[5, 10, 20, 30, 60].map((s) => <option key={s} value={s * 1000}>{s} s</option>)}</select></Field>
        <Field label="Alerts & confirms">
          <select value={form.dialogs} onChange={(e) => setForm({ dialogs: e.target.value })}><option value="accept">Accept</option><option value="dismiss">Dismiss</option></select></Field>
        <Field label="Slow motion">
          <select value={form.slowmo} onChange={(e) => setForm({ slowmo: +e.target.value })}>{[[0, "Off"], [100, "100 ms"], [300, "300 ms"], [800, "800 ms"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
      </div>
      <div className="opts">
        <Toggle k="video" label="Record video" /><Toggle k="shots" label="Screenshot every step" />
        <Toggle k="live" label="Live view" /><Toggle k="highlight" label="Highlight elements (green box)" />
        <Toggle k="fullpage" label="Full-page screenshots" />
        <Toggle k="heal" label="Self-heal locators" /><Toggle k="useLlm" label="Use AI (Claude)" />
        <Toggle k="headless" label="Headless" />
      </div>
    </>
  );
}

function Auth() {
  const { form, setForm, sessions } = useApp();
  return (
    <>
      <div className="grid2">
        <Field label="Start with saved session">
          <select value={form.session} onChange={(e) => setForm({ session: e.target.value })}>
            <option value="">None</option>{sessions.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}</select></Field>
        <Field label="Save session after run as"><input value={form.saveSession} placeholder="e.g. corp-sso" onChange={(e) => setForm({ saveSession: e.target.value })} /></Field>
        <Field label="Chrome profile folder" hint="Path on the machine that runs the test"><input value={form.profileDir} placeholder="C:\Users\me\AutomationProfile" onChange={(e) => setForm({ profileDir: e.target.value })} /></Field>
        <Field label="Profile name"><input value={form.profileName} placeholder="Default" onChange={(e) => setForm({ profileName: e.target.value })} /></Field>
      </div>
      <div className="callout"><b>Sessions</b> hold cookies and localStorage, and work with both Playwright and Selenium on any machine. To capture one, run headed
        (turn off <i>Headless</i>) with a step like <code>Pause 90 seconds for SSO login</code> and set <i>Save session after run as</i>.
        A <b>Chrome profile</b> reuses saved passwords and logged-in sites. Close Chrome on that profile first.</div>
    </>
  );
}

const TABS = [["script", "Steps"], ["data", "Test data"], ["options", "Settings"], ["auth", "Sign-in"]];

/** The settings that change what a run does, always visible above the editor (they used to hide in tabs). */
function Summary({ go }) {
  const { form } = useApp();
  const b = (BROWSERS.find(([v]) => v === form.browser) || [form.browser, form.browser])[1];
  const items = [b, form.video ? "video on" : "no video", `${(+form.timeout || 10000) / 1000} s per step`, form.heal ? "self-heal on" : "self-heal off",
    form.session ? `starts signed in: ${form.session}` : form.profileDir ? "Chrome profile" : "signed out", form.saveSession ? `saves login as ${form.saveSession}` : null].filter(Boolean);
  return <div className="runsum"><span className="mut">This run:</span>{items.map((x) => <span key={x} className="pill">{x}</span>)}
    <button className="btn ghost sm" onClick={() => go("options")}>Change</button></div>;
}

/** Before any run is open: a small placeholder and the latest Studio runs instead of a big empty browser. */
function Idle() {
  const { runs, nav } = useApp();
  const mine = runs.filter((r) => !r.batch_id).slice(0, 6);
  return <div className="studio-side">
    <div className="panel idlebox">
      <Icon name="eye" size={22} />
      <b>The browser shows here while a test runs</b>
      <span className="mut">From whichever machine runs it, with a screenshot for every step.</span>
    </div>
    <div className="panel">
      <div className="panel-h"><h3>Recent runs</h3><span className="spacer" /><a href="#/runs">All runs</a></div>
      {mine.length ? mine.map((r) => <a key={r.run_id} className="lrow" href={`#/studio/${encodeURIComponent(r.run_id)}`}
        onClick={(e) => { e.preventDefault(); nav("studio", r.run_id); }}><Status s={r.status} /><span className="ellipsis" style={{ flex: 1 }}>{r.test_name}</span>
        <span className="mut nowrap">{ago(r.started_at)}</span></a>) : <div className="empty-state">Your runs appear here.</div>}
    </div>
  </div>;
}

export default function Studio({ runId }) {
  const { form } = useApp();
  const [tab, setTab] = useState("script");
  const dataCount = form.dataMode === "iter" ? form.iterRows.filter((r) => r.on).length + "×" : form.rows.filter((r) => r.k.trim()).length;
  return (
    <section className="view on">
      <div className="studio">
        <div className="panel">
          <div className="tabs" role="tablist" aria-label="Test">
            {TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}{k === "data" && <span className="badge">{dataCount}</span>}</button>)}
          </div>
          <div className="panel-b">
            {tab !== "options" && <Summary go={setTab} />}
            {tab === "script" && <Script />}
            {tab === "data" && <DataEditor />}
            {tab === "options" && <Options />}
            {tab === "auth" && <Auth />}
          </div>
        </div>
        {runId ? <LiveViewer runId={runId} /> : <Idle />}
      </div>
    </section>
  );
}
