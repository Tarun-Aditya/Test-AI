import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

export default function Sessions() {
  const { sessions, loadSessions, setForm, nav, toast } = useApp();
  const imp = async (file) => {
    const name = prompt("Session name:", file.name.replace(/\.json$/, ""));
    if (!name) return;
    const fd = new FormData(); fd.append("file", file);
    try { await api(`/api/sessions/${encodeURIComponent(name)}`, { method: "POST", body: fd }); loadSessions(); toast("Session imported"); } catch (e) { toast(e.message, true); }
  };
  const del = async (n) => { if (!confirm(`Delete session ${n}?`)) return; await api(`/api/sessions/${encodeURIComponent(n)}`, { method: "DELETE" }); loadSessions(); };
  return (
    <section className="view on">
      <div className="cards">
        {sessions.map((s) => (
          <div className="card" key={s.name}>
            <h4><Icon name="key" style={{ color: "var(--acc)" }} />{s.name}</h4>
            <div className="row">{s.domains.length ? s.domains.map((d) => <span className="pill" key={d}>{d}</span>) : <span className="mut">no cookies</span>}</div>
            <div className="mut">Updated {new Date(s.updated * 1000).toLocaleString()}</div>
            <div className="row"><button className="btn sm" onClick={() => { setForm({ session: s.name }); nav("studio"); toast(`Next run starts with session “${s.name}”`); }}>Use in next run</button>
              <span className="spacer" /><button className="btn sm ghost danger" onClick={() => del(s.name)}>Delete</button></div>
          </div>
        ))}
        {!sessions.length && <div className="card"><h4>No saved sessions</h4><div className="mut">In Studio → Login &amp; SSO, set <i>Save session after run as</i>, or import a storage_state.json.</div></div>}
        <label className="drop"><Icon name="up" /><b>Import a session</b><span className="mut">Playwright storage_state.json (cookies + localStorage)</span>
          <input type="file" accept=".json" onChange={(e) => { e.target.files[0] && imp(e.target.files[0]); e.target.value = ""; }} /></label>
      </div>
    </section>
  );
}
