import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Icon from "../icons.jsx";

export default function Machines() {
  const { machines, health, form, setForm, nav, toast, loadMachines } = useApp();
  const cmd = `python -m aiauto agent --server ${location.origin} --name my-laptop --token <token>`;
  const use = (name) => { setForm({ agent: name }); nav("studio"); toast(`Tests will run on ${name === "local" ? health.local_name : name}`); };
  const forget = async (n) => { try { await api(`/api/agents/${encodeURIComponent(n)}`, { method: "DELETE" }); loadMachines(); } catch (e) { toast(e.message, true); } };
  const Card = ({ m, local }) => (
    <div className="card">
      <h4><span className={"dotc " + (local || m.online ? (m?.busy ? "warn" : "ok") : "")} />{local ? health.local_name : m.name}
        {local && <span className="pill">default</span>}{(local ? form.agent === "local" : form.agent === m.name) && <span className="pill" style={{ color: "var(--acc)" }}>selected</span>}</h4>
      <div className="mut">{local ? "this server" : `${m.hostname || ""} · ${m.platform || ""}`}</div>
      <div className="row">{(local ? ["playwright", "selenium"] : [...m.engines, ...m.browsers]).map((x) => <span className="pill" key={x}>{x}</span>)}</div>
      <div className="row" style={{ marginTop: "auto" }}>
        <span className="mut">{local ? "ready" : m.online ? (m.busy ? "busy" : "online, idle") : `offline · last seen ${new Date(m.last_seen * 1000).toLocaleString()}`}</span>
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
          <div className="copy"><span>{cmd}</span><button className="btn sm" onClick={() => navigator.clipboard?.writeText(cmd).then(() => toast("Copied"))}><Icon name="copy" size={14} />Copy</button></div>
        </div>
      </div>
    </section>
  );
}
