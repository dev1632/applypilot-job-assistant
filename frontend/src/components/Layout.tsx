import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { useAsync } from "../api/useAsync";

export function Layout() {
  const health = useAsync(() => api.health(), []);
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">ApplyPilot</span>
        <nav>
          <NavLink to="/" end>Jobs</NavLink>
          <NavLink to="/tailor">Paste a job</NavLink>
          <NavLink to="/profile">Profile &amp; scan</NavLink>
        </nav>
        <span className="spacer" />
        {health.error && <span className="badge tone-bad">API offline</span>}
        {health.data && (
          <span
            className={`badge outline tone-${health.data.llm_enabled ? "good" : "muted"}`}
            title={health.data.llm_enabled ? "OpenAI is used for analysis and tailoring" : "No OpenAI key: rule-based mode"}
          >
            {health.data.llm_enabled ? `OpenAI · ${health.data.model}` : "Rules only (no OpenAI key)"}
          </span>
        )}
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="alert tone-bad">{error}</div> : null;
}
