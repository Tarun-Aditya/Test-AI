import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";
import Integrations from "./Integrations.jsx";

const ORDER = ["auto", "claude-code", "cursor", "copilot", "anthropic", "litellm"];
const HELP = {
  "claude-code": <>Uses the Claude plan you are logged into on this machine through Claude Code: install it and run <code>claude</code> once to log in. No API key needed.</>,
  cursor: <>Uses your Cursor plan through the Cursor CLI: install it and run <code>cursor-agent login</code>. Model names are Cursor's (e.g. <code>sonnet-4.5</code>, <code>gpt-5</code>).</>,
  copilot: <>Uses your GitHub Copilot plan through the Copilot CLI: install it (<code>npm i -g @github/copilot</code>) and log in with <code>copilot</code>.</>,
  anthropic: <>An Anthropic API key (or set <code>ANTHROPIC_API_KEY</code> on the server). Structured outputs with automatic fallback if the model declines.</>,
  litellm: <>Any model LiteLLM supports, e.g. <code>openai/gpt-4o</code>, <code>gemini/gemini-2.5-pro</code>, <code>azure/my-deployment</code>,
    <code>bedrock/…</code>, <code>ollama/llama3</code>. Set a base URL to use a LiteLLM proxy or any OpenAI-compatible endpoint.</>,
};

function OrgAI() {
  const { toast, me } = useApp();
  const [ai, setAi] = useState(null);
  const [form, setForm] = useState({});
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    const a = await api("/api/ai");
    setAi(a);
    setForm({ provider: a.provider || "auto", model: a.model || "", litellm_model: a.litellm_model || "", litellm_base: a.litellm_base || "",
      anthropic_key: "", litellm_key: "", share_keys_with_agents: !!a.share_keys_with_agents,
      users_may_use_org: a.users_may_use_org !== false });
  };
  useEffect(() => { load().catch((e) => toast(e.message, true)); }, []);
  if (!ai) return <section className="view on"><div className="empty-state">Loading…</div></section>;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...form };
      for (const k of ["anthropic_key", "litellm_key"]) if (!body[k]) delete body[k];   // empty = keep the stored key
      const a = await api("/api/ai", { method: "PUT", body });
      setAi(a); setForm((f) => ({ ...f, anthropic_key: "", litellm_key: "" }));
      toast("AI settings saved");
    } catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const clearKey = async (k) => { const a = await api("/api/ai", { method: "PUT", body: { [k]: "" } }); setAi(a); toast("Key removed"); };
  const runTest = async () => {
    setBusy(true); setTest(null);
    try { const r = await api("/api/ai/test", { method: "POST" }); setTest(r); setAi((a) => ({ ...a, providers: r.providers, status: r.status })); }
    catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const p = form.provider;
  const shows = (id) => p === id || p === "auto";
  return (
    <section className="view on">
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="panel">
          <div className="panel-h"><h3>{me ? "Organisation AI" : "AI provider"}</h3></div>
          <div className="panel-b">
            <p className="hint" style={{ marginTop: 0 }}>The AI turns your English into steps, breaks ties between similar elements, and finds elements drawn on a canvas
              from a screenshot. Everything else runs without it. By default it uses <b>your own subscription</b>, then an API key, then LiteLLM.</p>
            <div className="field"><span>Provider</span>
              <div className="prov-list">
                {ORDER.map((id) => {
                  const st = ai.providers.find((x) => x.id === id);
                  return (
                    <label key={id} className={"mrow" + (p === id ? " on" : "")}>
                      <input type="radio" name="prov" checked={p === id} onChange={() => set("provider", id)} />
                      <div className="grow"><b>{ai.labels[id]}</b>{st && <small>{st.detail}</small>}</div>
                      {st && <span className={"slots " + (st.usable ? "ok" : "busy")}>{st.active ? "in use" : st.usable ? "ready" : "not set up"}</span>}
                    </label>
                  );
                })}
              </div>
              {HELP[p] && <small style={{ marginTop: 6 }}>{HELP[p]}</small>}
            </div>
            <div className="field"><span>Model</span>
              <input value={form.model} onChange={(e) => set("model", e.target.value)} placeholder="claude-opus-5-5 (default) · leave empty for the CLI's own default" spellCheck={false} />
              <small>Any model the chosen provider offers: <code>claude-opus-5-5</code>, <code>claude-sonnet-5-5</code>, <code>gpt-5</code>, …</small></div>
            {shows("anthropic") && <div className="field"><span>Anthropic API key {ai.anthropic_key_set && <span className="pill">saved</span>}</span>
              <div style={{ display: "flex", gap: 8 }}><input type="password" autoComplete="off" value={form.anthropic_key} onChange={(e) => set("anthropic_key", e.target.value)}
                placeholder={ai.anthropic_key_set ? "•••••• (stored on the hub; type to replace)" : "sk-ant-…"} style={{ flex: 1 }} />
                {ai.anthropic_key_set && <button className="btn sm ghost danger" onClick={() => clearKey("anthropic_key")}>Remove</button>}</div></div>}
            {shows("litellm") && <>
              <div className="grid2">
                <div className="field"><span>LiteLLM model</span><input value={form.litellm_model} onChange={(e) => set("litellm_model", e.target.value)} placeholder="openai/gpt-4o" spellCheck={false} /></div>
                <div className="field"><span>Base URL (optional)</span><input value={form.litellm_base} onChange={(e) => set("litellm_base", e.target.value)} placeholder="http://litellm-proxy:4000" spellCheck={false} /></div>
              </div>
              <div className="field"><span>LiteLLM / provider key {ai.litellm_key_set && <span className="pill">saved</span>}</span>
                <div style={{ display: "flex", gap: 8 }}><input type="password" autoComplete="off" value={form.litellm_key} onChange={(e) => set("litellm_key", e.target.value)}
                  placeholder={ai.litellm_key_set ? "•••••• (stored on the hub)" : "key for that provider (or set its usual env var on the server)"} style={{ flex: 1 }} />
                  {ai.litellm_key_set && <button className="btn sm ghost danger" onClick={() => clearKey("litellm_key")}>Remove</button>}</div></div>
            </>}
            {me && <label className="toggle" style={{ margin: "6px 0 8px" }}><input type="checkbox" checked={form.users_may_use_org} onChange={(e) => set("users_may_use_org", e.target.checked)} /><span className="sw" />
              People who haven't set their own AI (My AI) may use this organisation AI. Off: they need their own subscription or key.</label>}
            <label className="toggle" style={{ margin: "6px 0 14px" }}><input type="checkbox" checked={form.share_keys_with_agents} onChange={(e) => set("share_keys_with_agents", e.target.checked)} /><span className="sw" />
              Send API keys to remote machines with each run (only over HTTPS). Otherwise each machine uses its own subscription CLI or env key.</label>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn primary" disabled={busy} onClick={save}>Save</button>
              <button className="btn" disabled={busy} onClick={runTest}><Icon name="spark" size={14} />Test connection</button>
            </div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-h"><h3>Status</h3></div>
          <div className="panel-b">
            <p style={{ marginTop: 0 }}><b>{ai.status}</b></p>
            {test && <div className={"card " + (test.ok ? "" : "bad")} style={{ marginBottom: 12 }}>
              <h4>{test.ok ? "✓ Connected" : "✕ No answer"}</h4>
              <div className="mut">{test.provider ? `${ai.labels[test.provider]} answered in ${test.seconds}s` : test.status}</div>
            </div>}
            <p className="hint">Keys are stored only on the hub (in its database, owner-only file) and are never sent back to the browser.
              Saving here applies to new runs right away. Environment variables on a machine (<code>AIAUTO_AI_PROVIDER</code>, <code>AIAUTO_MODEL</code>,
              <code>ANTHROPIC_API_KEY</code>, <code>AIAUTO_LITELLM_*</code>) take precedence there.</p>
            <p className="hint">Without any provider, tests still run: steps come from the built-in rule parser and elements are chosen by heuristics.</p>
          </div>
        </div>
      </div>
    </section>
  );
}


function Users() {
  const { toast, me } = useApp();
  const [users, setUsers] = useState([]);
  const [f, setF] = useState({ username: "", name: "", email: "", role: "tester", password: "" });
  const load = () => api("/api/users").then(setUsers).catch((e) => toast(e.message, true));
  useEffect(() => { load(); }, []);
  const add = async (e) => {
    e.preventDefault();
    try { await api("/api/users", { method: "POST", body: f }); setF({ username: "", name: "", email: "", role: "tester", password: "" }); load(); toast("User added"); }
    catch (x) { toast(x.message, true); }
  };
  const upd = async (u, body, msg) => { try { await api(`/api/users/${u.id}`, { method: "PUT", body }); load(); toast(msg); } catch (x) { toast(x.message, true); } };
  const del = async (u) => { if (!window.confirm(`Delete ${u.username}? Their runs stay.`)) return; try { await api(`/api/users/${u.id}`, { method: "DELETE" }); load(); } catch (x) { toast(x.message, true); } };
  const reset = (u) => { const p = window.prompt(`New password for ${u.username} (at least 10 characters)`); if (p) upd(u, { password: p }, "Password reset; they were signed out"); };
  return (
    <div className="panel">
      <div className="panel-h"><h3>Users</h3><span className="mut">{users.length}</span></div>
      <div className="panel-b">
        <table className="dt"><thead><tr><th>User</th><th>E-mail</th><th>Role</th><th>Sign-in</th><th>Last seen</th><th /></tr></thead><tbody>
          {users.map((u) => <tr key={u.id} className={u.disabled ? "off" : ""}>
            <td><b>{u.name}</b><div className="mut">{u.username}{u.id === me.id ? " (you)" : ""}</div></td>
            <td>{u.email || "—"}</td>
            <td><select value={u.role} disabled={u.id === me.id} onChange={(e) => upd(u, { role: e.target.value }, "Role changed")}>
              {["admin", "tester", "viewer"].map((r) => <option key={r}>{r}</option>)}</select></td>
            <td>{[u.has_password && "password", u.sso && "SSO"].filter(Boolean).join(" + ") || "—"}</td>
            <td className="mut">{u.last_login ? new Date(u.last_login * 1000).toLocaleString() : "never"}</td>
            <td style={{ whiteSpace: "nowrap" }}>
              <button className="btn sm" onClick={() => reset(u)}>Set password</button>
              {u.id !== me.id && <button className="btn sm" onClick={() => upd(u, { disabled: !u.disabled }, u.disabled ? "Enabled" : "Disabled; signed out")}>{u.disabled ? "Enable" : "Disable"}</button>}
              {u.id !== me.id && <button className="btn sm ghost danger" onClick={() => del(u)}>Delete</button>}</td>
          </tr>)}</tbody></table>
        <form className="grid-add" onSubmit={add}>
          <input placeholder="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required />
          <input placeholder="name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <input placeholder="e-mail (links SSO sign-in)" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{["tester", "viewer", "admin"].map((r) => <option key={r}>{r}</option>)}</select>
          <input type="password" placeholder="password (blank = SSO only)" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          <button className="btn primary sm">Add user</button>
        </form>
        <p className="hint"><b>admin</b>: everything, including users, sign-in, organisation AI, machines and grids · <b>tester</b>: create, run and replay tests, own saved logins ·
          <b> viewer</b>: read runs, reports and videos. Changing a role or password signs that person out.</p>
      </div>
    </div>
  );
}

function SSO() {
  const { toast } = useApp();
  const [c, setC] = useState(null);
  const [secret, setSecret] = useState("");
  const load = () => api("/api/auth/sso-config").then((x) => setC({ enabled: false, auto_create: true, default_role: "tester", label: "Sign in with SSO", domains: [], ...x }));
  useEffect(() => { load().catch((e) => toast(e.message, true)); }, []);
  if (!c) return null;
  const set = (k, v) => setC({ ...c, [k]: v });
  const save = async () => {
    try {
      const body = { enabled: c.enabled, issuer: c.issuer || "", client_id: c.client_id || "", domains: c.domains, default_role: c.default_role,
        auto_create: c.auto_create, label: c.label };
      if (secret) body.client_secret = secret;
      setC({ ...c, ...(await api("/api/auth/sso-config", { method: "PUT", body })) }); setSecret(""); toast("Sign-in settings saved");
    } catch (e) { toast(e.message, true); }
  };
  return (
    <div className="panel">
      <div className="panel-h"><h3>Company sign-in (SSO)</h3></div>
      <div className="panel-b">
        <p className="hint" style={{ marginTop: 0 }}>OpenID Connect: Okta, Microsoft Entra ID (Azure AD), Google Workspace, Keycloak, Auth0, OneLogin, Ping…
          Create a web application there with this <b>redirect URI</b>, then paste its issuer, client ID and secret.</p>
        <div className="copy"><span>{c.redirect_uri}</span><button className="btn sm" onClick={() => navigator.clipboard?.writeText(c.redirect_uri).then(() => toast("Copied"))}><Icon name="copy" size={14} />Copy</button></div>
        <label className="toggle" style={{ margin: "12px 0" }}><input type="checkbox" checked={!!c.enabled} onChange={(e) => set("enabled", e.target.checked)} /><span className="sw" />Show the SSO button on the sign-in page</label>
        <div className="field"><span>Issuer URL</span><input className="mono" value={c.issuer || ""} onChange={(e) => set("issuer", e.target.value)}
          placeholder="https://login.microsoftonline.com/<tenant>/v2.0 · https://<org>.okta.com · https://accounts.google.com" /></div>
        <div className="grid2">
          <div className="field"><span>Client ID</span><input className="mono" value={c.client_id || ""} onChange={(e) => set("client_id", e.target.value)} /></div>
          <div className="field"><span>Client secret {c.client_secret_set && <span className="pill">saved</span>}</span>
            <input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={c.client_secret_set ? "•••••• (type to replace)" : ""} /></div>
        </div>
        <div className="grid2">
          <div className="field"><span>Allowed e-mail domains</span><input value={(c.domains || []).join(", ")} onChange={(e) => set("domains", e.target.value.split(",").map((d) => d.trim()).filter(Boolean))} placeholder="corp.com, corp.co.uk (empty = any)" /></div>
          <div className="field"><span>Role for new people</span><select value={c.default_role} onChange={(e) => set("default_role", e.target.value)}>{["tester", "viewer", "admin"].map((r) => <option key={r}>{r}</option>)}</select></div>
        </div>
        <div className="grid2">
          <div className="field"><span>Button label</span><input value={c.label || ""} onChange={(e) => set("label", e.target.value)} /></div>
          <label className="toggle" style={{ marginTop: 22 }}><input type="checkbox" checked={c.auto_create !== false} onChange={(e) => set("auto_create", e.target.checked)} /><span className="sw" />
            Create an account on first SSO sign-in (off: only people an admin added, matched by e-mail)</label>
        </div>
        <button className="btn primary" onClick={save}>Save sign-in settings</button>
      </div>
    </div>
  );
}

function Projects() {
  const { toast, loadProjects } = useApp();
  const [ps, setPs] = useState([]);
  const [users, setUsers] = useState([]);
  const [edit, setEdit] = useState(null);
  const load = async () => { setPs(await api("/api/projects")); setUsers(await api("/api/users")); };
  useEffect(() => { load().catch((e) => toast(e.message, true)); }, []);
  const save = async () => {
    try {
      const key = (edit.key || edit.name || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-|-$/g, "");
      if (!key) throw new Error("Give the project a name");
      await api(`/api/projects/${encodeURIComponent(key)}`, { method: "PUT", body: { name: edit.name || key, description: edit.description || "", members: edit.members } });
      setEdit(null); load(); loadProjects(); toast("Project saved");
    } catch (e) { toast(e.message, true); }
  };
  const del = async (p) => { if (!window.confirm(`Delete project "${p.name}"? Its ${p.runs} runs, scripts and saved logins move to Default.`)) return; await api(`/api/projects/${p.key}`, { method: "DELETE" }); load(); loadProjects(); };
  const toggle = (u) => setEdit((e) => ({ ...e, members: e.members.includes(u) ? e.members.filter((x) => x !== u) : [...e.members, u] }));
  return (
    <div className="panel">
      <div className="panel-h"><h3>Projects</h3><span className="spacer" /><button className="btn sm" onClick={() => setEdit({ isNew: true, name: "", description: "", members: [] })}>+ New project</button></div>
      <div className="panel-b">
        <p className="hint" style={{ marginTop: 0 }}>A project keeps a team's runs, reports, videos, library scripts and saved logins apart from other teams: members see
          only their projects (and <b>Default</b>, which is open to everyone). Admins see all. Machines, grids and AI settings are shared.</p>
        <table className="dt"><thead><tr><th>Project</th><th>Members</th><th>Runs</th><th /></tr></thead><tbody>
          {ps.map((p) => <tr key={p.key}><td><b>{p.name}</b><div className="mut">{p.key}{p.description ? ` · ${p.description}` : ""}</div></td>
            <td>{p.key === "default" ? <span className="mut">everyone</span> : (p.members || []).join(", ") || <span className="mut">admins only</span>}</td>
            <td>{p.runs}</td>
            <td style={{ whiteSpace: "nowrap" }}>{p.key !== "default" && <>
              <button className="btn sm" onClick={() => setEdit({ ...p, members: p.members || [] })}>Edit</button>
              <button className="btn sm ghost danger" onClick={() => del(p)}>Delete</button></>}</td></tr>)}</tbody></table>
        {edit && <div className="card" style={{ marginTop: 12 }}>
          <div className="grid2">
            <div className="field"><span>Name</span><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} disabled={!edit.isNew && false} placeholder="Payments" /></div>
            <div className="field"><span>Description</span><input value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></div>
          </div>
          <div className="field"><span>Members</span><div className="chips">
            {users.filter((u) => u.role !== "admin").map((u) => <label key={u.id} className={"chipsel" + (edit.members.includes(u.username) ? " on" : "")}>
              <input type="checkbox" checked={edit.members.includes(u.username)} onChange={() => toggle(u.username)} />{u.name} <small>{u.role}</small></label>)}
          </div><small>Admins see every project without being members.</small></div>
          <div style={{ display: "flex", gap: 8 }}><button className="btn primary" onClick={save}>Save project</button><button className="btn" onClick={() => setEdit(null)}>Cancel</button></div>
        </div>}
      </div>
    </div>
  );
}

export default function Settings() {
  const { me } = useApp();
  const [tab, setTab] = useState("ai");
  const tabs = me ? [["ai", "Organisation AI"], ["integrations", "Integrations"], ["sso", "Sign-in (SSO)"], ["users", "Users"], ["projects", "Projects"]]
    : [["ai", "AI provider"], ["integrations", "Integrations"]];          // single-user mode: no accounts to manage
  return (
    <>
      <div className="seg" style={{ margin: "16px 24px 0" }}>
        {tabs.map(([k, l]) =>
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "ai" && <OrgAI />}
      {tab === "integrations" && <section className="view on"><Integrations /></section>}
      {tab === "sso" && <section className="view on"><SSO /></section>}
      {tab === "users" && <section className="view on"><Users /></section>}
      {tab === "projects" && <section className="view on"><Projects /></section>}
    </>
  );
}
