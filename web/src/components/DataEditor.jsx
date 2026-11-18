import { useApp } from "../App.jsx";
import { fromJson, iterationsCsv, looksSecret, parseIterationsFile, placeholderKeys, toJson } from "../form.js";
import Icon from "../icons.jsx";

const ITER_BROWSERS = [["", "Default"], ["chromium", "Chromium"], ["chrome", "Chrome"], ["msedge", "Edge"], ["firefox", "Firefox"], ["webkit", "WebKit"]];

function download(name, text, type = "text/csv") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

function SingleTable() {
  const { form, setForm, toast } = useApp();
  const fromTest = () => {
    const have = new Set(form.rows.map((r) => r.k.trim()));
    const add = placeholderKeys(form.text).filter((k) => !have.has(k));
    if (!add.length) return toast(placeholderKeys(form.text).length ? "Every ${…} in the test already has a row" : "The test text has no ${…} placeholders");
    setForm((f) => ({ rows: [...f.rows.filter((r) => r.k.trim() || r.v), ...add.map((k) => ({ k, v: "", s: looksSecret(k) }))] }));
    toast(`Added ${add.join(", ")}`);
  };
  const upd = (i, patch) => setForm((f) => ({ rows: f.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  return (
    <>
      <table className="dt">
        <thead><tr><th style={{ width: "36%" }}>Key</th><th>Value</th><th style={{ width: 92 }} /><th style={{ width: 28 }} /></tr></thead>
        <tbody>
          {form.rows.map((r, i) => (
            <tr key={i}>
              <td><input className="k" value={r.k} placeholder="user.email" spellCheck={false} onChange={(e) => upd(i, { k: e.target.value })} /></td>
              <td><input className="v" value={r.v} type={r.s ? "password" : "text"} autoComplete="off" spellCheck={false} onChange={(e) => upd(i, { v: e.target.value })} /></td>
              <td><button className={"lock" + (r.s ? " on" : "")} onClick={() => upd(i, { s: !r.s })} title="Sensitive values are masked everywhere">
                <Icon name={r.s ? "lock" : "unlock"} size={13} />{r.s ? "Secret" : "Plain"}</button></td>
              <td><button className="x" title="Remove" onClick={() => setForm((f) => ({ rows: f.rows.filter((_, j) => j !== i) }))}><Icon name="x" size={14} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn sm" onClick={() => setForm((f) => ({ rows: [...f.rows, { k: "", v: "", s: false }] }))}><Icon name="plus" size={14} />Add value</button>
        <button className="btn sm" onClick={fromTest} title="Add a row for every ${…} placeholder used in the test">Keys from test</button>
      </div>
    </>
  );
}

function Iterations() {
  const { form, setForm, toast } = useApp();
  const { iterCols: cols, iterRows: rows } = form;
  const setRow = (i, patch) => setForm((f) => ({ iterRows: f.iterRows.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  const renameCol = (ci, key) => setForm((f) => {
    const old = f.iterCols[ci].key;
    return {
      iterCols: f.iterCols.map((c, j) => (j === ci ? { ...c, key } : c)),
      iterRows: f.iterRows.map((r) => { const v = { ...r.values, [key]: r.values[old] }; if (old !== key) delete v[old]; return { ...r, values: v }; }),
    };
  });
  const addRow = () => setForm((f) => {
    const prev = f.iterRows.at(-1);
    const values = prev ? { ...prev.values } : {};
    f.iterCols.filter((c) => c.sensitive).forEach((c) => (values[c.key] = ""));
    return { iterRows: [...f.iterRows, { on: true, browser: prev ? prev.browser : "", values }] };
  });
  const addCol = () => setForm((f) => ({ iterCols: [...f.iterCols, { key: `field${f.iterCols.length + 1}`, sensitive: false }],
    iterRows: f.iterRows.length ? f.iterRows : [{ on: true, browser: "", values: {} }] }));
  const shared = new Set(form.rows.filter((r) => r.k.trim() && r.v !== "").map((r) => r.k.trim()));
  const fromTest = () => {
    const keys = placeholderKeys(form.text);
    const have = new Set(cols.map((c) => c.key));
    const add = keys.filter((k) => !have.has(k) && !shared.has(k));
    if (!add.length) return toast(keys.length ? "Every ${…} in the test already has a column (or a shared Single-run value)" : "The test text has no ${…} placeholders");
    setForm((f) => ({ iterCols: [...f.iterCols, ...add.map((k) => ({ key: k, sensitive: looksSecret(k) }))],
      iterRows: f.iterRows.length ? f.iterRows : [{ on: true, browser: "", values: {} }] }));
    toast(`Added column${add.length > 1 ? "s" : ""} ${add.join(", ")}`);
  };
  const template = () => {
    let f = form;
    if (!f.iterCols.length) {     // no grid yet: template straight from the test's placeholders
      const keys = placeholderKeys(f.text).filter((k) => !shared.has(k));
      if (!keys.length) return toast("Add columns, or use ${…} placeholders in the test first", true);
      f = { ...f, iterCols: keys.map((k) => ({ key: k, sensitive: looksSecret(k) })), iterRows: [] };
    }
    download(`${(f.name || "test-data").replace(/[^\w.-]+/g, "_")}-iterations.csv`, iterationsCsv(f));
  };
  const upload = async (file) => {
    try {
      const parsed = parseIterationsFile(await file.text(), file.name);
      setForm(parsed);
      const secret = parsed.iterCols.filter((c) => c.sensitive).map((c) => c.key);
      toast(`Loaded ${parsed.iterRows.length} iteration${parsed.iterRows.length > 1 ? "s" : ""} × ${parsed.iterCols.length} column${parsed.iterCols.length > 1 ? "s" : ""}`
        + (secret.length ? ` · ${secret.join(", ")} marked secret` : ""));
    } catch (e) { toast(e.message, true); }
  };
  return (
    <>
      <div className="iter-tools">
        <button className="btn sm" onClick={fromTest} title="One column per ${…} placeholder in the test (skips keys set under Single run)">Columns from test</button>
        <button className="btn sm" onClick={template} title="CSV with the current columns and rows; secret columns are left empty"><Icon name="download" size={14} />Template (CSV)</button>
        <label className="btn sm" title="CSV (header row = keys, optional run / browser columns) or JSON list of rows"><Icon name="upload" size={14} />Upload CSV / JSON
          <input type="file" accept=".csv,.tsv,.txt,.json" hidden onChange={(e) => { const f = e.target.files[0]; if (f) upload(f); e.target.value = ""; }} /></label>
      </div>
      <div className="iter-wrap">
        <table className="it">
          <thead><tr><th>#</th><th>Run</th><th>Browser</th>
            {cols.map((c, ci) => (
              <th key={ci}><div className="colh">
                <input value={c.key} placeholder="key" onChange={(e) => renameCol(ci, e.target.value)} />
                <button className={"x" + (c.sensitive ? " warn" : "")} title={c.sensitive ? "Sensitive column" : "Mark column sensitive"}
                  onClick={() => setForm((f) => ({ iterCols: f.iterCols.map((x, j) => (j === ci ? { ...x, sensitive: !x.sensitive } : x)) }))}>
                  <Icon name={c.sensitive ? "lock" : "unlock"} size={13} /></button>
                <button className="x" title="Remove column" onClick={() => setForm((f) => ({ iterCols: f.iterCols.filter((_, j) => j !== ci) }))}><Icon name="x" size={13} /></button>
              </div></th>
            ))}<th /></tr></thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className={r.on ? "" : "off"}>
                <td className="mut">{ri + 1}</td>
                <td><input type="checkbox" checked={r.on} onChange={(e) => setRow(ri, { on: e.target.checked })} aria-label={`Run iteration ${ri + 1}`} /></td>
                <td><select value={r.browser} onChange={(e) => setRow(ri, { browser: e.target.value })}>{ITER_BROWSERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
                {cols.map((c, ci) => (
                  <td key={ci}><input type={c.sensitive ? "password" : "text"} autoComplete="off" value={r.values[c.key] ?? ""}
                    onChange={(e) => setRow(ri, { values: { ...r.values, [c.key]: e.target.value } })} /></td>
                ))}
                <td><button className="x" title="Remove iteration" onClick={() => setForm((f) => ({ iterRows: f.iterRows.filter((_, j) => j !== ri) }))}><Icon name="x" size={14} /></button></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={cols.length + 4} className="mut" style={{ padding: 14 }}>No iterations yet. Add one to run the same test several times with different data or browsers.</td></tr>}
          </tbody>
        </table>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <button className="btn sm" onClick={addRow}><Icon name="plus" size={14} />Add iteration</button>
        <button className="btn sm" onClick={addCol}><Icon name="plus" size={14} />Add column</button>
      </div>
      <p className="hint">Each enabled row is a separate run with its own data and <b>browser</b>. Values under <b>Single run</b> are shared by every iteration,
        and columns here override them. Click the lock icon on a column to mark it sensitive.
        Templates: a CSV header row lists the keys (<code>password [secret]</code> marks a secret column), with optional <code>run</code> (yes/no)
        and <code>browser</code> columns. Secret values are never written into downloaded templates.</p>
    </>
  );
}

export default function DataEditor() {
  const { form, setForm, toast } = useApp();
  const switchMode = (m) => {
    try {
      if (m === "json" && form.dataMode !== "json") setForm({ dataMode: m, ...toJson(form) });
      else if (form.dataMode === "json" && m !== "json") setForm({ dataMode: m, ...fromJson(form) });
      else setForm({ dataMode: m });
    } catch (e) { toast(e.message, true); }
  };
  const enabled = form.iterRows.filter((r) => r.on).length;
  return (
    <>
      <div className="seg" style={{ marginBottom: 12 }}>
        <button className={form.dataMode === "table" ? "on" : ""} onClick={() => switchMode("table")}>Single run</button>
        <button className={form.dataMode === "iter" ? "on" : ""} onClick={() => switchMode("iter")}>Iterations <span className="badge">{enabled}</span></button>
        <button className={form.dataMode === "json" ? "on" : ""} onClick={() => switchMode("json")}>JSON</button>
      </div>
      {form.dataMode === "table" && <>
        <SingleTable />
        <p className="hint">Reference values as <code>{"${key}"}</code>, and use dots to nest (<code>user.email</code>).
          Mark a value <b>Secret</b> to mask it in logs, reports, videos and generated scripts. Secrets are never stored in this browser.</p>
      </>}
      {form.dataMode === "iter" && <Iterations />}
      {form.dataMode === "json" && <>
        <div className="field"><textarea rows={11} className="mono" value={form.json} onChange={(e) => setForm({ json: e.target.value })}
          placeholder='{"user": {"email": "a@b.com", "password": {"value": "s3cret", "sensitive": true}}, "rows": [{"brand": "HP", "__browser": "firefox"}]}' /></div>
        <div className="field"><span>Sensitive keys</span><input value={form.sensKeys} onChange={(e) => setForm({ sensKeys: e.target.value })} placeholder="user.password, card.number" />
          <small>Or mark one inline with {'{"value": "…", "sensitive": true}'}. A "rows" list runs once per row, and "__browser" picks a row's browser.</small></div>
        <label className="drop" style={{ padding: 14 }}><b>Load a JSON file</b>
          <input type="file" accept=".json" onChange={async (e) => { const f = e.target.files[0]; if (f) setForm({ json: await f.text() }); e.target.value = ""; }} /></label>
      </>}
    </>
  );
}
