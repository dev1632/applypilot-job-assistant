import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAsync } from "../api/useAsync";
import { EligibilityBadge, FreshnessBadge, ScoreBar, StatusBadge } from "../components/Badges";
import { dateTime, humanStatus, relativeTime } from "../components/format";
import { ErrorNote } from "../components/Layout";

const TAILORABLE = new Set(["SHORTLISTED", "RESUME_READY", "REVIEW_REQUIRED", "CHANGES_REQUESTED"]);

export function JobDetailPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const job = useAsync(() => api.job(id), [id]);
  const resumes = useAsync(() => api.resumesForJob(id), [id]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");

  if (job.error) return <ErrorNote error={job.error} />;
  if (!job.data) return <p className="muted">Loading…</p>;
  const j = job.data;
  const score = j.latest_score;
  const app = j.application;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      await Promise.all([job.reload(), resumes.reload()]);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const generate = () =>
    run(async () => {
      const v = await api.tailor(j.id);
      navigate(`/resumes/${v.id}`);
    });

  return (
    <>
      <p className="crumbs"><Link to="/">← Jobs</Link></p>
      <div className="page-head">
        <div>
          <h1>{j.title}</h1>
          <p className="subtitle">
            {j.company} · {j.location ?? "Location not listed"}
            {j.employment_type ? ` · ${j.employment_type}` : ""}
          </p>
        </div>
        <StatusBadge status={app?.status ?? null} />
      </div>

      <ErrorNote error={actionError} />

      <div className="grid-2">
        <section className="card">
          <h2>Fit</h2>
          <div className="kv">
            <span>Score</span><ScoreBar score={score?.skills_score} />
            <span>Eligibility</span><EligibilityBadge value={score?.eligibility_status} />
            <span>Posted</span>
            <span>
              {j.posted_at ? `${relativeTime(j.posted_at)} (${dateTime(j.posted_at)})` : "No date from source"}{" "}
              <FreshnessBadge value={j.freshness_confidence} />
            </span>
            <span>Discovered</span><span>{dateTime(j.discovered_at)}</span>
            <span>Source</span>
            <span>
              {j.source} · <a href={j.url} target="_blank" rel="noreferrer noopener">Original posting ↗</a>
            </span>
            {j.possible_duplicate_of && (
              <>
                <span>Duplicate?</span>
                <Link to={`/jobs/${j.possible_duplicate_of}`}>Looks like job #{j.possible_duplicate_of}</Link>
              </>
            )}
          </div>
          {score && (
            <>
              <h3>Why it matches</h3>
              <List items={score.match_reasons} empty="No specific matches found" />
              <h3>Gaps</h3>
              <List items={score.gaps} empty="No gaps detected" />
              <h3>Eligibility notes</h3>
              <List items={score.eligibility_notes} empty="None" tone />
            </>
          )}
        </section>

        <section className="card">
          <h2>Resume</h2>
          {resumes.data && resumes.data.length > 0 ? (
            <ul className="versions">
              {resumes.data.map((v) => (
                <li key={v.id}>
                  <Link to={`/resumes/${v.id}`}>Version {v.version}</Link>
                  {app?.resume_version_id === v.id && <span className="badge outline tone-info">current</span>}
                  <span className={`badge tone-${v.review_status === "APPROVED" ? "good" : v.review_status === "REJECTED" ? "bad" : "warn"}`}>
                    {humanStatus(v.review_status)}
                  </span>
                  <span className="muted small">{dateTime(v.created_at)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No tailored resume yet.</p>
          )}
          <button
            className="primary"
            disabled={busy || !app || (!TAILORABLE.has(app.status) && j.source !== "pasted")}
            onClick={generate}
            title={app && !TAILORABLE.has(app.status) ? `Not available while ${humanStatus(app.status)}` : undefined}
          >
            {busy ? "Working…" : resumes.data?.length ? "Generate new version" : "Generate tailored resume"}
          </button>

          {app && (
            <>
              <h2 className="spaced">Application</h2>
              <p className="small muted">Fill the employer's form in a sandboxed browser (dry run), review every answer, then approve.</p>
              <Link className="button" to={`/applications/${app.id}`}>Open application review →</Link>
            </>
          )}

          {app && j.allowed_next.length > 0 && (
            <>
              <h2 className="spaced">Move this application</h2>
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (target && reason.trim())
                    void run(async () => {
                      await api.transition(app.id, target, reason.trim());
                      setTarget("");
                      setReason("");
                    });
                }}
              >
                <select value={target} onChange={(e) => setTarget(e.target.value)} required>
                  <option value="">Choose a status…</option>
                  {j.allowed_next.map((s) => (
                    <option key={s} value={s}>{humanStatus(s)}</option>
                  ))}
                </select>
                <input
                  placeholder="Reason (saved in the history)"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                />
                <button disabled={busy || !target || !reason.trim()}>Move</button>
              </form>
            </>
          )}

          <h2 className="spaced">History</h2>
          <ol className="timeline">
            {j.events.map((e) => (
              <li key={e.id}>
                <span className="small muted">{dateTime(e.timestamp)}</span>
                <span>
                  {e.old_status ? `${humanStatus(e.old_status)} → ` : ""}
                  <strong>{humanStatus(e.new_status)}</strong>
                </span>
                {e.details && <span className="small">{e.details}</span>}
              </li>
            ))}
          </ol>
        </section>
      </div>

      <section className="card">
        <details>
          <summary><h2 className="inline-h">Job description</h2></summary>
          <p className="muted small">Copied from the source. Treated as untrusted text.</p>
          <pre className="description">{j.description || "No description captured."}</pre>
        </details>
      </section>
    </>
  );
}

function List({ items, empty, tone }: { items: string[]; empty: string; tone?: boolean }) {
  if (!items.length) return <p className="muted small">{empty}</p>;
  return (
    <ul className="plain">
      {items.map((t, i) => {
        const cls = tone ? (t.startsWith("FAIL") ? "tone-text-bad" : t.startsWith("REVIEW") ? "tone-text-warn" : "") : "";
        return <li key={i} className={cls}>{t}</li>;
      })}
    </ul>
  );
}
