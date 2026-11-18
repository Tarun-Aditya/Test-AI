import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import App from "./App.jsx";
import Login from "./views/Login.jsx";

/** Signed in? Show the app. Otherwise the sign-in page (or first-time setup). AIAUTO_AUTH=off: always the app. */
export default function Root() {
  const [me, setMe] = useState(null);
  const load = useCallback(() => api("/api/auth/me").then(setMe).catch(() => setMe({ auth: true, user: null, error: true })), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const out = () => setMe((m) => (m && m.auth ? { ...m, user: null } : m));
    window.addEventListener("aiauto:signed-out", out);
    return () => window.removeEventListener("aiauto:signed-out", out);
  }, []);
  if (!me) return <div className="boot">Loading…</div>;
  if (me.auth && !me.user) return <Login info={me} onDone={load} />;
  return <App me={me.auth ? me.user : null} reloadMe={load} />;
}
