import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

export default function Library() {
  const { library, loadLibrary, toast, setForm, nav, startRun } = useApp();
  const upload = async (file) => {
    const fd = new FormData(); fd.append("file", file);
    try { const r = await api("/api/library/upload", { method: "POST", body: fd }); await loadLibrary(); toast(`Uploaded “${r.name}” (${r.steps} steps)`); }
    catch (e) { toast(e.message, true); }
  };
  const del = async (n) => { if (!confirm(`Delete ${n}?`)) return; await api(`/api/library/${encodeURIComponent(n)}`, { method: "DELETE" }); loadLibrary(); };
  return (
    <section className="view on">
      <div className="cards">
        {library.map((l) => (
          <div className="card" key={l.name}>
            <h4><Icon name="lib" style={{ color: "var(--acc)" }} />{l.name}</h4>
            <div className="mut">{l.test_name}</div>
            <div className="row"><span className="pill">{l.steps} steps</span>{l.engine && <span className="pill">recorded with {l.engine}</span>}
              {l.sensitive.length > 0 && <span className="pill" style={{ color: "var(--warn)" }}>🔒 {l.sensitive.join(", ")}</span>}
              {l.data_keys.length > 0 && <span className="pill">data: {l.data_keys.join(", ")}</span>}</div>
            <div className="row" style={{ marginTop: "auto" }}>
              <button className="btn sm primary" onClick={() => startRun({ library: l.name })}><Icon name="play" size={12} />Run</button>
              <button className="btn sm" onClick={() => { setForm({ srcMode: "lib", libName: l.name }); nav("studio"); }}>Open in Studio</button>
              <a className="btn sm" href={`/api/library/${encodeURIComponent(l.name)}`} target="_blank" rel="noreferrer">JSON</a>
              <span className="spacer" /><button className="btn sm ghost danger" onClick={() => del(l.name)}>Delete</button>
            </div>
          </div>
        ))}
        <label className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); }}>
          <Icon name="up" /><b>Upload a script</b><span className="mut">script.json, or a generated test_playwright.py / test_selenium.py</span>
          <input type="file" accept=".json,.py" onChange={(e) => { e.target.files[0] && upload(e.target.files[0]); e.target.value = ""; }} />
        </label>
      </div>
      {!library.length && <p className="hint" style={{ marginTop: 16 }}>Save a passing run's generated script from its details (＋ Library), or upload one. Library scripts run in <b>Script mode</b>, with no AI parsing.</p>}
    </section>
  );
}
