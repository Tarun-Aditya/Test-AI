import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

const OPTS = [
  ["org", "Use the organisation's AI", "Whatever your admin set up (a company key or gateway)."],
  ["claude-code", "My Claude subscription", "Pro / Max / Team plan through Claude Code."],
  ["copilot", "My GitHub Copilot subscription", "Through the Copilot CLI."],
  ["cursor", "My Cursor subscription", "Through the Cursor CLI."],
  ["anthropic", "My Anthropic API key", "Billed to your Anthropic account."],
  ["litellm", "Any model (LiteLLM)", "OpenAI, Gemini, Azure, Bedrock, Ollama, a proxy… with your key."],
  ["none", "No AI for my runs", "Steps come from the rule parser and elements from heuristics."],
];
const SECRET = { "claude-code": "claude_token", copilot: "copilot_token", cursor: "cursor_key", anthropic: "anthropic_key", litellm: "litellm_key" };
const HOW = {
  "claude-code": <>On your computer run <code>claude setup-token</code> (needs your Claude subscription), copy the token it prints and paste it here.
    It lets this platform use <b>your</b> plan for <b>your</b> runs only.</>,
  copilot: <>Create a GitHub token (fine-grained, with the <b>Copilot Requests</b> permission) on github.com → Settings → Developer settings, and paste it here.</>,
  cursor: <>Create an API key in the Cursor dashboard (Integrations / API keys) and paste it here.</>,
  anthropic: <>An API key from console.anthropic.com.</>,
  litellm: <>The model as LiteLLM names it, e.g. <code>openai/gpt-4o</code> or <code>gemini/gemini-2.5-pro</code>, and that provider's key.</>,
};

export default function MyAI() {
  const { toast, me } = useApp();
  const [s, setS] = useState(null);
  const [f, setF] = useState({});
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => { const x = await api("/api/me/ai"); setS(x); setF({ provider: x.provider, model: x.model || "", litellm_model: x.litellm_model || "", litellm_base: x.litellm_base || "", secret: "" }); };
  useEffect(() => { load().catch((e) => toast(e.message, true)); }, []);
  if (!s) return <section className="view on"><div className="empty-state">Loading…</div></section>;
  const p = f.provider;
  const sk = SECRET[p];
  const save = async () => {
    setBusy(true);
    try {
      const body = { provider: p, model: f.model, litellm_model: f.litellm_model, litellm_base: f.litellm_base };
      if (sk && f.secret) body[sk] = f.secret;
      const x = await api("/api/me/ai", { method: "PUT", body });
      setS(x); setF((o) => ({ ...o, secret: "" })); toast("Saved. Your runs use this from now on");
    } catch (e) { toast(e.message, true); } finally { setBusy(false); }
  };
  const forget = async () => { const x = await api("/api/me/ai", { method: "PUT", body: { [sk]: "" } }); setS(x); toast("Removed"); };
  const runTest = async () => { setBusy(true); setTest(null); try { setTest(await api("/api/me/ai/test", { method: "POST" })); } catch (e) { toast(e.message, true); } finally { setBusy(false); } };
  return (
    <section className="view on">
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="panel">
          <div className="panel-h"><h3>My AI</h3><span className="mut">{me.name || me.username}</span></div>
          <div className="panel-b">
            <p className="hint" style={{ marginTop: 0 }}>The AI that works on <b>your</b> runs: turning your English into steps, breaking ties between similar
              elements, and reading screenshots for canvas elements. It's tied to your account, wherever your test runs (this server, an agent, Docker).</p>
            <div className="field"><span>Use</span>
              <div className="prov-list">
                {OPTS.filter(([id]) => id !== "org" || s.org_allowed).map(([id, label, sub]) => (
                  <label key={id} className={"mrow" + (p === id ? " on" : "")}>
                    <input type="radio" name="myprov" checked={p === id} onChange={() => setF({ ...f, provider: id, secret: "" })} />
                    <div className="grow"><b>{label}</b><small>{sub}</small></div>
                    {SECRET[id] && s[`${SECRET[id]}_set`] && <span className="slots ok">saved</span>}
                  </label>))}
              </div>
              {!s.org_allowed && <small>Your admin requires everyone to use their own AI.</small>}
            </div>
            {HOW[p] && <p className="hint">{HOW[p]}</p>}
            {p === "litellm" && <div className="grid2">
              <div className="field"><span>Model</span><input className="mono" value={f.litellm_model} onChange={(e) => setF({ ...f, litellm_model: e.target.value })} placeholder="openai/gpt-4o" /></div>
              <div className="field"><span>Base URL (optional)</span><input className="mono" value={f.litellm_base} onChange={(e) => setF({ ...f, litellm_base: e.target.value })} placeholder="https://my-gateway/v1" /></div>
            </div>}
            {sk && <div className="field"><span>{p === "claude-code" ? "Claude token" : p === "copilot" ? "GitHub token" : p === "cursor" ? "Cursor API key" : "API key"}
              {s[`${sk}_set`] && <span className="pill">saved</span>}</span>
              <div style={{ display: "flex", gap: 8 }}>
                <input type="password" autoComplete="off" style={{ flex: 1 }} value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })}
                  placeholder={s[`${sk}_set`] ? "•••••• (stored encrypted; type to replace)" : "paste it here"} />
                {s[`${sk}_set`] && <button className="btn sm ghost danger" onClick={forget}>Remove</button>}</div></div>}
            {p !== "org" && p !== "none" && p !== "litellm" && <div className="field"><span>Model (optional)</span>
              <input className="mono" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder={p === "claude-code" || p === "anthropic" ? "claude-opus-5-5" : "the CLI's default"} /></div>}
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn primary" disabled={busy} onClick={save}>Save</button>
              <button className="btn" disabled={busy} onClick={runTest}><Icon name="spark" size={14} />Test my AI</button>
            </div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-h"><h3>Status</h3></div>
          <div className="panel-b">
            <p style={{ marginTop: 0 }}><b>{s.status}</b></p>
            {test && <div className="card" style={{ marginBottom: 12 }}><h4>{test.ok ? "✓ Connected" : "✕ No answer"}</h4>
              <div className="mut">{test.ok ? `answered in ${test.seconds}s` : test.status}</div></div>}
            <p className="hint">Your token or key is stored encrypted on the server and never shown again in the app, not even to admins. It is used
              only for runs you start, on any machine: agents send your AI calls back to the server. The subscription CLI runs with your token in a private
              profile, so nobody else's login is used for you and yours for nobody else.</p>
            <p className="hint">With <b>Use the organisation's AI</b>, an agent on your own computer where you're logged into Claude Code / Cursor /
              Copilot uses that login first.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
