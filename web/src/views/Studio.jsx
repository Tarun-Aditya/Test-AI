import { useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import DataEditor from "../components/DataEditor.jsx";
import LiveViewer from "../components/LiveViewer.jsx";
import { BROWSERS, EXAMPLES, dataPayload } from "../form.js";
import Icon from "../icons.jsx";

function Toggle({ k, label }) {
  const { form, setForm } = useApp();
  return <label className="toggle"><input type="checkbox" checked={!!form[k]} onChange={(e) => setForm({ [k]: e.target.checked })} /><span className="sw" />{label}</label>;
}
function Field({ label, hint, children }) {
  return <div className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</div>;
}

function Script() {
  const { form, setForm, library, toast, loadLibrary } = useApp();
  const [preview, setPreview] = useState(null);
  const gutter = useRef(null);
  const lines = Math.max(1, form.text.split("\n").length);
  const doPreview = async () => {
    try {
      const d = dataPayload(form);
      const data = d.iterations ? { ...(d.data || {}), ...d.iterations[0].data } : d.data;
      setPreview(await api("/api/parse", { method: "POST", body: { text: form.text, data, sensitive: d.sensitive, use_llm: form.useLlm } }));
    } catch (e) { toast(e.message, true); }
  };
  const upload = async (file) => {
    const fd = new FormData(); fd.append("file", file);
    try { const r = await api("/api/library/upload", { method: "POST", body: fd }); await loadLibrary(); setForm({ libName: r.name }); toast(`Uploaded “${r.name}” (${r.steps} steps)`); }
    catch (e) { toast(e.message, true); }
  };
  return (
    <>
      <div className="seg" style={{ marginBottom: 12 }}>
        <button className={form.srcMode === "text" ? "on" : ""} onClick={() => setForm({ srcMode: "text" })}>Plain English</button>
        <button className={form.srcMode === "lib" ? "on" : ""} onClick={() => setForm({ srcMode: "lib" })}>From library</button>
      </div>
      {form.srcMode === "text" ? <>
        <input className="name-in" value={form.name || ""} placeholder="Test name (e.g. Add product to cart)" onChange={(e) => setForm({ name: e.target.value })} aria-label="Test name" />
        <div className="editor">
          <div className="gutter" ref={gutter}>{Array.from({ length: lines }, (_, i) => i + 1).join("\n")}</div>
          <textarea spellCheck={false} value={form.text} onChange={(e) => setForm({ text: e.target.value })}
            onScroll={(e) => gutter.current && (gutter.current.scrollTop = e.currentTarget.scrollTop)}
            placeholder={'Open https://shop.example.com\nClick Accept cookies if shown\nType "${product}" into the search box\nPress Enter\nClick the first result containing "${brand}"\nClick Add to Cart button\nVerify page contains "Added to Cart"'} />
        </div>
        <div className="examples">
          <span className="mut">Examples:</span>
          {Object.keys(EXAMPLES).map((k) => <button key={k} onClick={() => setForm({ text: EXAMPLES[k] })}>{k}</button>)}
          <span className="spacer" />
          <button className="btn sm" onClick={doPreview}><Icon name="eye" size={14} />Preview steps</button>
        </div>
      </> : <>
        <Field label="Saved script" hint="Runs with the stored native locators. Steps re-identify their element (self-heal) if the page changed.">
          <select value={form.libName} onChange={(e) => setForm({ libName: e.target.value })}>
            <option value="">Choose a script…</option>
            {library.map((l) => <option key={l.name} value={l.name}>{l.name} · {l.steps} steps</option>)}
          </select></Field>
        <label className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); }}>
          <Icon name="up" /><b>Upload a script</b><span className="mut">script.json, or a generated test_playwright.py / test_selenium.py</span>
          <input type="file" accept=".json,.py" onChange={(e) => { e.target.files[0] && upload(e.target.files[0]); e.target.value = ""; }} />
        </label>
      </>}
      {preview && form.srcMode === "text" && <>
        <div className="hint" style={{ marginTop: 14 }}>{preview.test.steps.length} steps · parsed by <b>{preview.parser}</b></div>
        <div className="plist">
          {preview.test.steps.map((s, i) => (
            <div className="pstep" key={i}><span className="n">{i + 1}</span><span className="tag">{s.action}</span>
              <div>{s.target}{s.nth != null && <span className="pill"> #{s.nth < 0 ? "last" : s.nth + 1}</span>}{s.optional && <span className="pill"> optional</span>} <span className="val">{s.value}</span></div></div>
          ))}
        </div>
        {preview.unparsed.length > 0 && <div className="callout" style={{ background: "var(--bad-soft)", color: "var(--bad)" }}>Not understood: {preview.unparsed.join(" · ")}</div>}
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
        <Toggle k="live" label="Live view" /><Toggle k="fullpage" label="Full-page screenshots" />
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

const TABS = [["script", "Script"], ["data", "Test data"], ["options", "Options"], ["auth", "Login & SSO"]];

export default function Studio({ runId }) {
  const { form } = useApp();
  const [tab, setTab] = useState("script");
  const dataCount = form.dataMode === "iter" ? form.iterRows.filter((r) => r.on).length + "×" : form.rows.filter((r) => r.k.trim()).length;
  return (
    <section className="view on">
      <div className="studio">
        <div className="panel">
          <div className="tabs">
            {TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}{k === "data" && <span className="badge">{dataCount}</span>}</button>)}
          </div>
          <div className="panel-b">
            {tab === "script" && <Script />}
            {tab === "data" && <DataEditor />}
            {tab === "options" && <Options />}
            {tab === "auth" && <Auth />}
          </div>
        </div>
        <LiveViewer runId={runId} />
      </div>
    </section>
  );
}
