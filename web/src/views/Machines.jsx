import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

export default function Machines() {
  const { machines, health, form, setForm, nav, toast, loadMachines } = useApp();
  const cmd = `python -m aiauto agent --server ${location.origin} --name my-laptop --token <token> --slots 2`;
  const dcmd = `docker run -d --shm-size=2g autopilot agent --server ${location.origin} --name docker-1 --token <token> --slots 2`;
  const use = (name) => { setForm({ agent: name, agents: [name] }); nav("studio"); toast(`Tests will run on ${name === "local" ? health.local_name : name}`); };
  const picked = (n) => (form.agents || [form.agent]).includes(n);
  const forget = async (n) => { try { await api(`/api/agents/${encodeURIComponent(n)}`, { method: "DELETE" }); loadMachines(); } catch (e) { toast(e.message, true); } };
  const Card = ({ m, local }) => (
    <div className="card">
      <h4><span className={"dotc " + (local || m.online ? (m?.busy ? "warn" : "ok") : "")} />{local ? health.local_name : m.name}
        {local && <span className="pill">default</span>}{picked(local ? "local" : m.name) && <span className="pill" style={{ color: "var(--acc)" }}>selected</span>}</h4>
      <div className="mut">{local ? "this server" : `${m.hostname || ""} · ${m.platform || ""}`}</div>
      <div className="row">{(local ? ["playwright", "selenium"] : [...m.engines, ...m.browsers]).map((x) => <span className="pill" key={x}>{x}</span>)}</div>
      <div className="row" style={{ marginTop: "auto" }}>
        <span className="mut">{local ? `${health.local_running || 0} running · ${health.local_slots || 1} parallel slots`
          : m.online ? `${m.running || 0} running · ${m.slots || 1} parallel slot${(m.slots || 1) > 1 ? "s" : ""}${m.queued ? ` · ${m.queued} waiting` : ""}`
          : `offline · last seen ${new Date(m.last_seen * 1000).toLocaleString()}`}</span>
        <span className="spacer" />
        {!local && !m.online && <button className="btn sm ghost danger" onClick={() => forget(m.name)}>Forget</button>}
        {(local || m.online) && <button className="btn sm" onClick={() => use(local ? "local" : m.name)}>Run tests here</button>}
      </div>
    </div>
  );
  return (
    <section className="view on">
      <div className="cards"><Card local />{machines.map((m) => <Card key={m.name} m={m} />)}</div>
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-h"><h3>Add a machine</h3></div>
        <div className="panel-b">
          <p className="hint" style={{ marginTop: 0 }}>Install this project on any computer with browsers (laptop, VM, CI runner), then connect it. It opens an outbound
            connection, so it works behind firewalls and NAT, and it appears in the machine picker. Registered machines are remembered and show as offline when disconnected.</p>
          {[["On a computer", cmd], ["In Docker (Playwright + Selenium + browsers included)", dcmd]].map(([l, c]) => (
            <div key={l} style={{ marginBottom: 10 }}><small className="mut">{l}</small>
              <div className="copy"><span>{c}</span><button className="btn sm" onClick={() => navigator.clipboard?.writeText(c).then(() => toast("Copied"))}><Icon name="copy" size={14} />Copy</button></div></div>))}
          <p className="hint"><code>--slots N</code> lets a machine run N tests at the same time (each in its own browser, Playwright or Selenium).
            Pick several machines in the machine picker to run on each of them, or to share iterations as a pool.</p>
        </div>
      </div>
    </section>
  );
}
