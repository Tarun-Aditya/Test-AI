import { useState } from "react";
import { api } from "../api.js";
import Icon from "../icons.jsx";

export default function Login({ info, onDone }) {
  const setup = info.setup_required;
  const [f, setF] = useState({ username: "", password: "", code: "", name: "" });
  const [err, setErr] = useState(() => decodeURIComponent((location.hash.match(/error=([^&]+)/) || [])[1] || ""));
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await api(setup ? "/api/auth/setup" : "/api/auth/login", { method: "POST", body: setup ? f : { username: f.username, password: f.password } });
      if (location.hash.includes("login")) location.hash = "/";
      onDone();
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand" style={{ marginBottom: 18 }}><div className="logo"><Icon name="spark" /></div><div><b>Autopilot</b><small>AI test automation</small></div></div>
        <h2>{setup ? "Create the admin account" : "Sign in"}</h2>
        {setup && <p className="hint">First start: enter the <b>setup code</b> printed in the server's log, then choose the admin's username and password.</p>}
        {info.sso?.enabled && !setup && <>
          <a className="btn primary block" href="/api/auth/sso/start">{info.sso.label}</a>
          <div className="or"><span>or with a password</span></div>
        </>}
        {setup && <label className="field"><span>Setup code</span><input value={f.code} onChange={set("code")} autoComplete="one-time-code" placeholder="xxxxxx-xxxxxx-xxxxxx" required /></label>}
        <label className="field"><span>Username</span><input value={f.username} onChange={set("username")} autoComplete="username" autoFocus required /></label>
        {setup && <label className="field"><span>Your name</span><input value={f.name} onChange={set("name")} autoComplete="name" /></label>}
        <label className="field"><span>Password</span><input type="password" value={f.password} onChange={set("password")}
          autoComplete={setup ? "new-password" : "current-password"} minLength={setup ? 10 : undefined} required />
          {setup && <small>At least 10 characters.</small>}</label>
        {err && <div className="login-err" role="alert">{err}</div>}
        <button className="btn primary block" disabled={busy}>{setup ? "Create admin and sign in" : "Sign in"}</button>
      </form>
    </div>
  );
}
