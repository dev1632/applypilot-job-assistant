import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { DraftBullet, DraftEntry, Profile, ResumeReport } from "../api/types";
import { useAsync } from "../api/useAsync";
import { AtsCard } from "../components/AtsCard";
import { StatusBadge } from "../components/Badges";
import { dateTime, humanStatus } from "../components/format";
import { ErrorNote } from "../components/Layout";

/** Issues name their location, e.g. "exp:acme bullet 2: uses leadership wording…" or
 *  "Replaced exp:acme bullet 2 with your original wording (…)". */
function issuesFor(list: string[], where: string) {
  const escaped = where.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(^|\\s)${escaped}(:|\\s)`);
  return list.filter((s) => re.test(s)).map((s) => s.replace(`${where}: `, ""));
}

function factLookup(profile: Profile | null, report: ResumeReport | null) {
  const facts: Record<string, string> = { ...(report?.cited_facts ?? {}) };
  for (const e of profile?.experience ?? []) e.bullets.forEach((b, i) => (facts[`exp:${e.id}:b${i}`] ??= b));
  for (const p of profile?.projects ?? []) {
    facts[`proj:${p.id}:desc`] ??= p.description;
    p.outcomes.forEach((o, i) => (facts[`proj:${p.id}:out${i}`] ??= o));
  }
  return facts;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const month = (v: string | null) => (v ? `${MONTHS[Number(v.slice(5, 7)) - 1]} ${v.slice(0, 4)}` : "Present");

export function ResumeReviewPage() {
  const id = Number(useParams().id);
  const resume = useAsync(() => api.resume(id), [id]);
  const jobId = resume.data?.job_id;
  const job = useAsync(() => (jobId ? api.job(jobId) : Promise.resolve(null)), [jobId]);
  const profile = useAsync(() => api.profile(), []);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (resume.error) return <ErrorNote error={resume.error} />;
  if (!resume.data) return <p className="muted">Loading…</p>;
  const v = resume.data;
  const report = v.report;
  const content = v.content;
  const facts = factLookup(profile.data, report);
  const claims = report?.claims_requiring_review ?? [];
  const corrections = report?.auto_corrections ?? [];
  const isCurrent = job.data?.application?.resume_version_id === v.id;
  const needsConfirm = claims.length > 0;

  const decide = async (approve: boolean) => {
    setBusy(true);
    setError(null);
    try {
      resume.setData(await api.reviewResume(v.id, approve, note));
      await job.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const entryHeader = (entryId: string) => {
    const [kind, key] = entryId.split(":");
    if (kind === "exp") {
      const e = profile.data?.experience.find((x) => x.id === key);
      return e ? { title: `${e.title}, ${e.organization}`, meta: `${month(e.start)} – ${month(e.end)}` } : { title: entryId, meta: "" };
    }
    const p = profile.data?.projects.find((x) => x.id === key);
    return p ? { title: p.name, meta: p.technologies.join(", ") } : { title: entryId, meta: "" };
  };

  const renderBullet = (b: DraftBullet, where: string) => {
    const flagged = issuesFor(claims, where);
    const fixed = issuesFor(corrections, where);
    const sources = b.source_fact_ids.map((f) => ({ id: f, text: facts[f] }));
    const verbatim = sources.length === 1 && sources[0].text === b.text;
    return (
      <li key={where} className={flagged.length ? "bullet flagged" : "bullet"}>
        <div className="bullet-text">{b.text}</div>
        <div className="bullet-meta">
          <span className={`badge outline tone-${verbatim ? "muted" : "info"}`}>
            {verbatim ? "your original wording" : "reworded"}
          </span>
          {flagged.map((m, i) => <span key={i} className="badge tone-warn" title={m}>needs review</span>)}
          {fixed.length > 0 && <span className="badge tone-info" title={fixed.join("\n")}>auto-corrected</span>}
        </div>
        {flagged.map((m, i) => <p key={i} className="small tone-text-warn">⚠ {m}</p>)}
        {fixed.map((m, i) => <p key={i} className="small muted">↺ {m}</p>)}
        {!verbatim && (
          <details className="sources">
            <summary className="small">Source facts ({sources.length})</summary>
            <ul>
              {sources.map((s) => (
                <li key={s.id} className="small">
                  <code>{s.id}</code> {s.text ?? <em>fact no longer in profile</em>}
                </li>
              ))}
            </ul>
          </details>
        )}
      </li>
    );
  };

  const section = (title: string, entries: DraftEntry[]) =>
    entries.length > 0 && (
      <>
        <h3 className="resume-h">{title}</h3>
        {entries.map((e) => {
          const h = entryHeader(e.entry_id);
          return (
            <div key={e.entry_id} className="resume-entry">
              <div className="entry-line"><strong>{h.title}</strong><span className="muted">{h.meta}</span></div>
              <ul className="bullets">{e.bullets.map((b, i) => renderBullet(b, `${e.entry_id} bullet ${i + 1}`))}</ul>
            </div>
          );
        })}
      </>
    );

  const kc = report?.quality.keyword_coverage;

  return (
    <>
      <p className="crumbs">
        <Link to="/">← Jobs</Link>
        {job.data && <> / <Link to={`/jobs/${job.data.id}`}>{job.data.company}: {job.data.title}</Link></>}
      </p>
      <div className="page-head">
        <div>
          <h1>Resume review · v{v.version}</h1>
          <p className="subtitle">
            Created {dateTime(v.created_at)} ·{" "}
            {report?.method === "openai" ? `Drafted with OpenAI (${report.model})` : "Selected from your facts (no rewording)"}
          </p>
        </div>
        <StatusBadge status={job.data?.application?.status ?? null} />
      </div>
      <ErrorNote error={error} />

      <div className="review-layout">
        <article className="card resume-preview" aria-label="Resume preview">
          <div className="resume-name">{profile.data?.name || "Your name"}</div>
          {content?.summary && (
            <>
              <h3 className="resume-h">Summary</h3>
              <ul className="bullets">{renderBullet(content.summary, "summary")}</ul>
            </>
          )}
          {content && content.skills.length > 0 && (
            <>
              <h3 className="resume-h">Skills</h3>
              <p>{content.skills.join(", ")}</p>
            </>
          )}
          {content && section("Experience", content.experiences)}
          {content && section("Projects", content.projects)}
          <p className="small muted">Education and contact details come straight from your profile and appear in the DOCX.</p>
        </article>

        <aside className="stack-lg">
          <section className="card">
            <h2>Decision</h2>
            <p>
              Review status:{" "}
              <span className={`badge tone-${v.review_status === "APPROVED" ? "good" : v.review_status === "REJECTED" ? "bad" : "warn"}`}>
                {humanStatus(v.review_status)}
              </span>
            </p>
            <p className="small muted">
              Content hash <code title={v.content_hash}>{v.content_hash.slice(0, 12)}…</code>. Any later change makes a new
              version that needs its own review.
            </p>
            {!isCurrent && job.data && (
              <div className="alert tone-warn">This isn't the application's current resume, so it can't be reviewed.</div>
            )}
            {needsConfirm && (
              <label className="check">
                <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                {claims.length === 1
                  ? "I checked the flagged claim and it is accurate"
                  : `I checked the ${claims.length} flagged claims and they are accurate`}
              </label>
            )}
            <textarea placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
            <div className="row">
              <button
                className="primary"
                disabled={busy || !isCurrent || (needsConfirm && !confirmed) || v.review_status === "APPROVED"}
                onClick={() => decide(true)}
              >
                Approve resume
              </button>
              <button className="danger" disabled={busy || !isCurrent || v.review_status === "REJECTED"} onClick={() => decide(false)}>
                Reject
              </button>
              <a className="button" href={api.resumeDownloadUrl(v.id)}>Download DOCX</a>
            </div>
          </section>

          {report?.ats && <AtsCard ats={report.ats} changes={report.ats_changes ?? []} />}

          <section className="card">
            <h2>Needs your review <span className="count">{claims.length}</span></h2>
            {claims.length ? <ul className="plain">{claims.map((c, i) => <li key={i} className="tone-text-warn">{c}</li>)}</ul>
              : <p className="muted small">Nothing flagged.</p>}
            {corrections.length > 0 && (
              <>
                <h3>Blocked and auto-corrected</h3>
                <p className="small muted">These model edits weren't supported by your facts, so your original wording was used.</p>
                <ul className="plain small">{corrections.map((c, i) => <li key={i}>{c}</li>)}</ul>
              </>
            )}
          </section>

          <section className="card">
            <h2>Job match</h2>
            {kc && kc.ratio !== null && (
              <div className="coverage">
                <span>Keyword coverage</span>
                <span className="score-track wide"><span className="score-fill" style={{ width: `${Math.round(kc.ratio * 100)}%` }} /></span>
                <span>{Math.round(kc.ratio * 100)}%</span>
              </div>
            )}
            {kc && (
              <div className="chips">
                {kc.covered.map((s) => <span key={s} className="chip tone-good">✓ {s}</span>)}
                {kc.missing.map((s) => <span key={s} className="chip tone-muted">✗ {s}</span>)}
              </div>
            )}
            <h3>Gaps (not claimed)</h3>
            {report?.gaps.length ? <ul className="plain">{report.gaps.map((g) => <li key={g}>{g}</li>)}</ul>
              : <p className="muted small">None</p>}
            <h3>Matched requirements</h3>
            <ul className="plain small">
              {report?.matched_requirements.map((m) => (
                <li key={m.requirement}><strong>{m.requirement}</strong> <span className="muted">← {m.fact_ids.join(", ")}</span></li>
              ))}
            </ul>
          </section>

          <section className="card">
            <h2>Checks</h2>
            <CheckList title="Chronology" items={report?.quality.chronology ?? []} />
            <CheckList title="Formatting" items={report?.quality.formatting ?? []} />
            {report?.change_summary.length ? (
              <>
                <h3>What changed</h3>
                <ul className="plain small">{report.change_summary.map((c, i) => <li key={i}>{c}</li>)}</ul>
              </>
            ) : null}
          </section>
        </aside>
      </div>
    </>
  );
}

function CheckList({ title, items }: { title: string; items: string[] }) {
  return (
    <p className="small">
      <strong>{title}:</strong>{" "}
      {items.length ? <span className="tone-text-warn">{items.join("; ")}</span> : <span className="tone-text-good">OK</span>}
    </p>
  );
}
