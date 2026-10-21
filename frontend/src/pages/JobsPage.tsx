import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAsync } from "../api/useAsync";
import { EligibilityBadge, FreshnessBadge, ScoreBar, StatusBadge } from "../components/Badges";
import { relativeTime } from "../components/format";
import { ErrorNote } from "../components/Layout";

const TABS: { label: string; status?: string }[] = [
  { label: "All" },
  { label: "Needs review", status: "REVIEW_REQUIRED" },
  { label: "Shortlisted", status: "SHORTLISTED" },
  { label: "Resume ready", status: "RESUME_READY" },
  { label: "Prepared", status: "APPLICATION_PREPARED" },
  { label: "Awaiting approval", status: "AWAITING_APPROVAL" },
  { label: "Approved", status: "APPROVED" },
  { label: "Submitted", status: "SUBMITTED" },
  { label: "Excluded", status: "EXCLUDED" },
];

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? undefined;
  const freshness = params.get("freshness") ?? undefined;
  const jobs = useAsync(() => api.jobs({ status, freshness }), [status, freshness]);
  const stats = useAsync(() => api.stats(), []);
  const total = Object.values(stats.data ?? {}).reduce((a, b) => a + b, 0);

  const set = (key: string, value?: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <>
      <div className="page-head">
        <h1>Jobs</h1>
        <label className="inline">
          Posting date
          <select value={freshness ?? ""} onChange={(e) => set("freshness", e.target.value || undefined)}>
            <option value="">Any confidence</option>
            <option value="VERIFIED">Verified</option>
            <option value="ESTIMATED">Estimated</option>
            <option value="UNVERIFIED">Unverified</option>
          </select>
        </label>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((t) => {
          const count = t.status ? stats.data?.[t.status] ?? 0 : total;
          return (
            <button
              key={t.label}
              role="tab"
              aria-selected={status === t.status}
              className={status === t.status ? "tab active" : "tab"}
              onClick={() => set("status", t.status)}
            >
              {t.label} <span className="count">{count}</span>
            </button>
          );
        })}
      </div>

      <ErrorNote error={jobs.error} />
      {jobs.loading && !jobs.data && <p className="muted">Loading…</p>}
      {jobs.data && jobs.data.length === 0 && (
        <div className="empty">
          No jobs here yet. Add sources on the <Link to="/profile">Profile &amp; scan</Link> page and run a scan.
        </div>
      )}
      {jobs.data && jobs.data.length > 0 && (
        <div className="table-wrap">
          <table className="jobs">
            <thead>
              <tr>
                <th>Role</th>
                <th>Location</th>
                <th>Posted</th>
                <th>Fit</th>
                <th>Eligibility</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => (
                <tr key={j.id}>
                  <td>
                    <Link to={`/jobs/${j.id}`} className="role">
                      <span className="title">{j.title}</span>
                      <span className="company">{j.company}</span>
                    </Link>
                  </td>
                  <td className="muted">{j.location ?? "—"}</td>
                  <td>
                    <span className="nowrap">{j.posted_at ? relativeTime(j.posted_at) : "no date"}</span>{" "}
                    <FreshnessBadge value={j.freshness_confidence} />
                  </td>
                  <td><ScoreBar score={j.latest_score?.skills_score} /></td>
                  <td><EligibilityBadge value={j.latest_score?.eligibility_status} /></td>
                  <td><StatusBadge status={j.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
