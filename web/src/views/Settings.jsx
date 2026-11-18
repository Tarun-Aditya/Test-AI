import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

const ORDER = ["auto", "claude-code", "cursor", "copilot", "anthropic", "litellm"];
const HELP = {
  "claude-code": <>Uses the Claude plan you are logged into on this machine through Claude Code: install it and run <code>claude</code> once to log in. No API key needed.</>,
  cursor: <>Uses your Cursor plan through the Cursor CLI: install it and run <code>cursor-agent login</code>. Model names are Cursor's (e.g. <code>sonnet-4.5</code>, <code>gpt-5</code>).</>,
  copilot: <>Uses your GitHub Copilot plan through the Copilot CLI: install it (<code>npm i -g @github/copilot</code>) and log in with <code>copilot</code>.</>,
  anthropic: <>An Anthropic API key (or set <code>ANTHROPIC_API_KEY</code> on the server). Structured outputs with automatic fallback if the model declines.</>,
  litellm: <>Any model LiteLLM supports, e.g. <code>openai/gpt-4o</code>, <code>gemini/gemini-2.5-pro</code>, <code>azure/my-deployment</code>,
    <code>bedrock/…</code>, <code>ollama/llama3</code>. Set a base URL to use a LiteLLM proxy or any OpenAI-compatible endpoint.</>,
};

export default function Settings() {
  const { toast } = useApp();
  const [ai, setAi] = useState(null);
  const [form, setForm] = useState({});
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    const a = await api("/api/ai");
    setAi(a);
    setForm({ provider: a.provider || "auto", model: a.model || "", litellm_model: a.litellm_model || "", litellm_base: a.litellm_base || "",
      anthropic_key: "", litellm_key: "", share_keys_with_agents: !!a.share_keys_with_agents });
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
          <div className="panel-h"><h3>AI provider</h3></div>
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
