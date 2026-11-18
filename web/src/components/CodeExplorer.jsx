import { useEffect, useMemo, useState } from "react";
import { api, art } from "../api.js";
import Icon from "../icons.jsx";

const TEXT = /\.(py|json|txt|md|html|js|css|sh|bat|csv|log|ya?ml)$/i;
const IMG = /\.(png|jpe?g|gif|webp|svg)$/i;
const VID = /\.(webm|mp4)$/i;
const ORDER = ["test_playwright.py", "test_selenium.py", "script.json", "data.json", "summary.json", "report.html"];

function buildTree(files) {
  const root = { name: "", dirs: {}, files: [] };
  files.forEach((f) => {
    const parts = f.path.split("/");
    let node = root;
    parts.slice(0, -1).forEach((p) => (node = node.dirs[p] = node.dirs[p] || { name: p, dirs: {}, files: [] }));
    node.files.push({ ...f, name: parts.at(-1) });
  });
  return root;
}
const fmt = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n > 1024 ? `${(n / 1024).toFixed(0)} KB` : `${n} B`);

/** Tiny highlighter for Python / JSON (escaped first, then wrapped). */
function highlight(code, path) {
  const esc = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  if (/\.json$/.test(path))
    return esc.replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?)/g, (m, s, colon, kw, num) =>
      s ? `<span class="${colon ? "tk-key" : "tk-str"}">${s}</span>${colon || ""}` : kw ? `<span class="tk-kw">${kw}</span>` : `<span class="tk-num">${num}</span>`);
  if (/\.py$/.test(path))
    return esc.replace(/(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|in|not|and|or|is|None|True|False|lambda|raise|assert|pass|global)\b|\b(\d+(?:\.\d+)?)\b/g,
      (m, com, str, kw, num) => com ? `<span class="tk-com">${com}</span>` : str ? `<span class="tk-str">${str}</span>` : kw ? `<span class="tk-kw">${kw}</span>` : `<span class="tk-num">${num}</span>`);
  return esc;
}

function Dir({ node, depth, open, toggle, sel, onPick, filter }) {
  const names = Object.keys(node.dirs).sort();
  const files = [...node.files].sort((a, b) => (ORDER.indexOf(a.name) + 1 || 99) - (ORDER.indexOf(b.name) + 1 || 99) || a.name.localeCompare(b.name));
  return <>
    {names.map((n) => {
      const d = node.dirs[n], key = (node.path || "") + n + "/";
      const isOpen = filter || open[key] !== false;
      return <div key={key}>
        <button className="tree-row" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => toggle(key)}>
          <span className="caret">{isOpen ? "▾" : "▸"}</span><Icon name="layers" size={14} />{n}<span className="mut">{Object.keys(d.dirs).length + d.files.length}</span></button>
        {isOpen && <Dir node={{ ...d, path: key }} depth={depth + 1} open={open} toggle={toggle} sel={sel} onPick={onPick} filter={filter} />}
      </div>;
    })}
    {files.map((f) => (
      <button key={f.path} className={"tree-row file" + (sel === f.path ? " on" : "")} style={{ paddingLeft: 22 + depth * 14 }} onClick={() => onPick(f.path)} title={f.path}>
        <Icon name={IMG.test(f.name) || VID.test(f.name) ? "video" : "doc"} size={14} /><span className="ellipsis">{f.name}</span><span className="mut">{fmt(f.size)}</span></button>
    ))}
  </>;
}

/** IDE-style explorer for everything a run generated: file tree, name filter, full-text search, code view. */
export default function CodeExplorer({ runId }) {
  const [files, setFiles] = useState(null);
  const [sel, setSel] = useState(null);
  const [content, setContent] = useState("");
  const [filter, setFilter] = useState("");
  const [grep, setGrep] = useState("");
  const [hits, setHits] = useState(null);
  const [open, setOpen] = useState({ "screenshots/": false });
  const [cache] = useState(() => new Map());

  useEffect(() => {
    api(`/api/runs/${encodeURIComponent(runId)}/files`).then((f) => { setFiles(f); setSel(f.find((x) => x.path === "test_playwright.py")?.path || f[0]?.path); })
      .catch(() => setFiles([]));
  }, [runId]);
  const read = async (path) => {
    if (cache.has(path)) return cache.get(path);
    const t = await (await fetch(art(runId, path))).text();
    cache.set(path, t);
    return t;
  };
  useEffect(() => { if (sel && TEXT.test(sel) && !/\.html$/.test(sel)) read(sel).then(setContent); else setContent(""); }, [sel]);

  const visible = useMemo(() => (files || []).filter((f) => !filter || f.path.toLowerCase().includes(filter.toLowerCase())), [files, filter]);
  const tree = useMemo(() => buildTree(visible), [visible]);
  const runGrep = async (q) => {
    setGrep(q);
    if (q.length < 2) return setHits(null);
    const out = [];
    for (const f of (files || []).filter((x) => TEXT.test(x.path) && x.size < 400000 && !/\.html$/.test(x.path))) {
      const lines = (await read(f.path)).split("\n");
      lines.forEach((l, i) => { if (l.toLowerCase().includes(q.toLowerCase())) out.push({ path: f.path, line: i + 1, text: l.trim().slice(0, 160) }); });
    }
    setHits(out.slice(0, 300));
  };
  const jump = (path, line) => { setSel(path); setTimeout(() => document.getElementById(`L${line}`)?.scrollIntoView({ block: "center" }), 150); };

  if (files === null) return <div style={{ padding: 16 }} aria-busy="true"><div className="skel" style={{ height: 14 }} /><div className="skel" style={{ height: 14, marginTop: 10, width: "70%" }} /></div>;
  if (!files.length) return <div className="empty-state">No files yet. Artifacts appear once the run finishes (agents upload them at the end).</div>;
  const lines = content ? highlight(content, sel).split("\n") : [];
  return (
    <div className="explorer">
      <div className="ex-side">
        <label className="search"><Icon name="search" size={16} /><input className="in" placeholder="Go to file…" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
        <label className="search"><Icon name="doc" size={16} /><input className="in" placeholder="Search in files…" value={grep} onChange={(e) => runGrep(e.target.value)} /></label>
        <div className="tree">
          {hits ? (hits.length ? hits.map((h, i) => (
            <button key={i} className="tree-row hit" onClick={() => jump(h.path, h.line)}><span className="mono mut">{h.path}:{h.line}</span><span className="ellipsis">{h.text}</span></button>
          )) : <div className="mut" style={{ padding: 10 }}>No matches</div>)
            : <Dir node={tree} depth={0} open={open} toggle={(k) => setOpen((o) => ({ ...o, [k]: o[k] === false }))} sel={sel} onPick={setSel} filter={!!filter} />}
        </div>
      </div>
      <div className="ex-main">
        <div className="ex-bar"><span className="mono ellipsis">{sel}</span><span className="spacer" />
          {sel && <><a className="btn sm" href={art(runId, sel)} target="_blank" rel="noreferrer">Open raw</a>
            <a className="btn sm" href={art(runId, sel)} download><Icon name="up" size={13} style={{ transform: "rotate(180deg)" }} />Download</a></>}</div>
        <div className="ex-view">
          {sel && IMG.test(sel) && <img src={art(runId, sel)} alt={sel} style={{ maxWidth: "100%" }} />}
          {sel && VID.test(sel) && <video controls src={art(runId, sel)} style={{ width: "100%" }} />}
          {sel && /\.html$/.test(sel) && <iframe title={sel} src={art(runId, sel)} style={{ width: "100%", height: "100%", border: 0, background: "#fff" }} />}
          {lines.length > 0 && <pre className="code"><code>{lines.map((l, i) => (
            <div key={i} id={`L${i + 1}`} className={grep && l.toLowerCase().includes(grep.toLowerCase()) ? "hl" : ""}><span className="ln">{i + 1}</span><span dangerouslySetInnerHTML={{ __html: l || " " }} /></div>
          ))}</code></pre>}
        </div>
      </div>
    </div>
  );
}
