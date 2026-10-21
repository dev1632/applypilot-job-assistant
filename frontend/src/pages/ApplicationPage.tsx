import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Answer, ApplicationDetail } from "../api/types";
import { useAsync } from "../api/useAsync";
import { StatusBadge } from "../components/Badges";
import { dateTime, humanStatus } from "../components/format";
import { ErrorNote } from "../components/Layout";

const PREPARABLE = new Set(["RESUME_READY", "APPLICATION_PREPARED", "CHANGES_REQUESTED", "INVALIDATED", "FAILED", "REVIEW_REQUIRED"]);
const EDITABLE = new Set(["APPLICATION_PREPARED", "REVIEW_REQUIRED", "AWAITING_APPROVAL", "APPROVED", "CHANGES_REQUESTED", "INVALIDATED"]);
const short = (h: string | null | undefined) => (h ? `${h.slice(0, 12)}…` : "—");

const GROUPS: { title: string; hint: string; match: (a: Answer) => boolean }[] = [
  { title: "Needs your answer", hint: "ApplyPilot won't guess these. Answer them yourself.", match: (a) => a.verification_status === "NEEDS_USER" },
  { title: "Drafts to confirm", hint: "Written by the model from your verified facts. Edit, fill any [ADD: …] placeholders, then confirm.", match: (a) => a.verification_status === "DRAFT" },
  { title: "Filled", hint: "From your verified profile, approved answers, or your own confirmations.", match: (a) => a.verification_status === "VERIFIED" || a.verification_status === "USER_CONFIRMED" },
  { title: "Left blank", hint: "Optional questions ApplyPilot leaves empty unless you pre-approve an answer.", match: (a) => a.verification_status === "SKIPPED" },
];

export function ApplicationPage() {
  const id = Number(useParams().id);
  const detail = useAsync(() => api.application(id), [id]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [reviewed, setReviewed] = useState(false);

  useEffect(() => setReviewed(false), [detail.data?.application.form_snapshot_hash]);
  useEffect(() => {
    if (detail.data && !url) setUrl(detail.data.application_url ?? detail.data.job.url);
  }, [detail.data, url]);

  if (detail.error) return <ErrorNote error={detail.error} />;
  if (!detail.data) return <p className="muted">Loading…</p>;
  const d = detail.data;
  const status = d.application.status;
  const prep = d.prep;

  const act = async (fn: () => Promise<ApplicationDetail | unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const out = await fn();
      if (out && typeof out === "object" && "application" in out) detail.setData(out as ApplicationDetail);
      else await detail.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const resumeOk = d.resume?.review_status === "APPROVED";
  const checklist = [
    { label: `Resume v${d.resume?.version ?? "?"} approved`, ok: resumeOk },
    { label: "Every question answered", ok: !!prep && d.outstanding.needs_answer.length === 0 },
    { label: "Every draft confirmed by you", ok: !!prep && d.outstanding.drafts_to_confirm.length === 0 },
    { label: "Job verified on the application page", ok: !!prep && prep.job_checks.filter((c) => !c.advisory).every((c) => c.ok) },
    { label: "No CAPTCHA, block, or browser error", ok: !!prep && prep.pause_reasons.length === 0 },
  ];
  const activeApproval = d.approvals.find((a) => a.approval_status === "ACTIVE");

  return (
    <>
      <p className="crumbs">
        <Link to="/">← Jobs</Link> / <Link to={`/jobs/${d.job.id}`}>{d.job.company}: {d.job.title}</Link>
      </p>
      <div className="page-head">
        <div>
          <h1>Application review</h1>
          <p className="subtitle">{d.job.company} · {d.job.title}{d.job.location ? ` · ${d.job.location}` : ""}</p>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="alert tone-info">
        <strong>Dry-run mode.</strong> ApplyPilot fills the form in a sandboxed browser that blocks every submit and
        outgoing request. It never submits for you.
      </div>
      <ErrorNote error={error} />

      <div className="grid-2">
        <section className="card">
          <h2>Dry-run fill</h2>
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => api.prepare(d.job.id, url));
            }}
          >
            <label className="small muted" htmlFor="app-url">Application page URL</label>
            <input id="app-url" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="primary" disabled={busy || !PREPARABLE.has(status) || !resumeOk}
              title={!resumeOk ? "Approve the resume first" : !PREPARABLE.has(status) ? `Not available while ${humanStatus(status)}` : undefined}>
              {busy ? "Filling in sandboxed browser…" : prep ? `Re-run dry run (#${prep.run + 1})` : "Run dry-run fill"}
            </button>
            {!resumeOk && d.resume && <p className="small tone-text-warn">Approve <Link to={`/resumes/${d.resume.id}`}>resume v{d.resume.version}</Link> first.</p>}
          </form>
          {prep && (
            <>
              <div className="kv spaced">
                <span>Run</span><span>#{prep.run} · {dateTime(prep.prepared_at)}</span>
                <span>Page</span><span className="break">{prep.page_title || prep.final_url}</span>
                <span>Fields found</span><span>{prep.fields_found}</span>
                <span>Blocked</span>
                <span title={prep.blocked_requests.join("\n")}>
                  {prep.blocked_submits} submit attempt{prep.blocked_submits === 1 ? "" : "s"}, {prep.blocked_requests.length} outgoing request{prep.blocked_requests.length === 1 ? "" : "s"}
                </span>
              </div>
              <h3>Job verification</h3>
              <ul className="plain">
                {prep.job_checks.map((c) => (
                  <li key={c.check} className={c.ok ? "tone-text-good" : c.advisory ? "muted" : "tone-text-bad"}>
                    {c.ok ? "✓" : "✗"} {c.check}: {c.expected}{c.advisory && !c.ok ? " (advisory)" : ""}
                  </li>
                ))}
              </ul>
              {prep.pause_reasons.length > 0 && (
                <div className="alert tone-warn spaced">
                  <strong>Paused:</strong>
                  <ul className="plain">{prep.pause_reasons.map((p) => <li key={p}>{p}</li>)}</ul>
                </div>
              )}
            </>
          )}
        </section>

        <section className="card">
          <h2>Filled form</h2>
          {prep?.screenshot ? (
            <a href={api.screenshotUrl(d.application.id, prep.run)} target="_blank" rel="noreferrer">
              <img className="shot" src={api.screenshotUrl(d.application.id, prep.run)} alt="Screenshot of the filled application form" />
            </a>
          ) : (
            <p className="muted">Run the dry run to see the filled form.</p>
          )}
        </section>
      </div>

      {prep && (
        <section className="card">
          <h2>Answers</h2>
          {GROUPS.map((g) => {
            const rows = d.answers.filter(g.match);
            if (!rows.length) return null;
            return (
              <div key={g.title} className="answer-group">
                <h3>{g.title} <span className="count">{rows.length}</span></h3>
                <p className="small muted">{g.hint}</p>
                {rows.map((a) => (
                  <AnswerRow key={a.question_key} a={a} disabled={busy || !EDITABLE.has(status)}
                    onSave={(value, save) => act(() => api.answer(d.application.id, a.question_key, value, save))} />
                ))}
              </div>
            );
          })}
        </section>
      )}

      <section className="card approval">
        <h2>Approval</h2>
        <ul className="checklist">
          {checklist.map((c) => <li key={c.label} className={c.ok ? "tone-text-good" : "muted"}>{c.ok ? "✓" : "○"} {c.label}</li>)}
        </ul>

        {status === "APPLICATION_PREPARED" && (
          <button className="primary" disabled={busy || !checklist.every((c) => c.ok)} onClick={() => act(() => api.ready(d.application.id))}>
            Send to final approval
          </button>
        )}

        {status === "AWAITING_APPROVAL" && d.resume && (
          <div className="stack">
            <p>You're approving this exact application. Any change afterwards cancels the approval.</p>
            <div className="kv">
              <span>Job</span><span>#{d.job.id} {d.job.company}: {d.job.title}</span>
              <span>Resume</span><span>v{d.resume.version} · <code>{short(d.resume.content_hash)}</code></span>
              <span>Form answers</span><span><code>{short(d.application.form_snapshot_hash)}</code> ({d.answers.filter((a) => a.answer).length} answers)</span>
            </div>
            <label className="check">
              <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
              I reviewed every answer above and the resume, and I approve this application
            </label>
            <div className="row">
              <button className="primary" disabled={busy || !reviewed}
                onClick={() => act(() => api.approve(d.application.id, d.job.id, d.resume!.content_hash, d.application.form_snapshot_hash!))}>
                Approve application
              </button>
            </div>
          </div>
        )}

        {status === "APPROVED" && activeApproval && (
          <div className="stack">
            <div className="alert tone-good">
              Approved {dateTime(activeApproval.approved_at)} for resume <code>{short(activeApproval.resume_hash)}</code> and
              answers <code>{short(activeApproval.form_hash)}</code>.
            </div>
            <div className="row">
              <button disabled title="Submission is disabled in dry-run mode">Submit application</button>
              <a className="button" href={d.application_url ?? d.job.url} target="_blank" rel="noreferrer noopener">
                Open employer's page to apply yourself ↗
              </a>
            </div>
            <p className="small muted">Dry-run mode: ApplyPilot records your approval but doesn't submit.</p>
          </div>
        )}

        {d.approvals.length > 0 && (
          <>
            <h3>Approval history</h3>
            <ul className="plain small">
              {d.approvals.map((a) => (
                <li key={a.id}>
                  {dateTime(a.approved_at)} · <span className={a.approval_status === "INVALIDATED" ? "tone-text-bad" : ""}>{humanStatus(a.approval_status)}</span>
                  {" "}· resume <code>{short(a.resume_hash)}</code> · answers <code>{short(a.form_hash)}</code>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

function AnswerRow({ a, disabled, onSave }: { a: Answer; disabled: boolean; onSave: (v: string | null, save: boolean) => void }) {
  const open = a.verification_status === "NEEDS_USER" || a.verification_status === "DRAFT";
  const [editing, setEditing] = useState(open);
  const [value, setValue] = useState(a.answer ?? "");
  const [save, setSave] = useState(false);
  useEffect(() => {
    setValue(a.answer ?? "");
    setEditing(a.verification_status === "NEEDS_USER" || a.verification_status === "DRAFT");
  }, [a.answer, a.verification_status]);

  const fileField = a.field_type === "file";
  const input =
    a.options.length > 0 ? (
      <select aria-label={a.question_text} value={value} onChange={(e) => setValue(e.target.value)}>
        <option value="">Choose…</option>
        {a.options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    ) : a.field_type === "checkbox" ? (
      <label className="check">
        <input type="checkbox" aria-label={a.question_text} checked={value === "Yes"} onChange={(e) => setValue(e.target.checked ? "Yes" : "")} />
        Check this box
      </label>
    ) : a.field_type === "textarea" || a.category === "narrative" ? (
      <textarea aria-label={a.question_text} rows={5} value={value} onChange={(e) => setValue(e.target.value)} />
    ) : (
      <input aria-label={a.question_text} value={value} onChange={(e) => setValue(e.target.value)} />
    );

  return (
    <div className={`answer ${a.sensitive ? "sensitive" : ""} status-${a.verification_status.toLowerCase()}`}>
      <div className="answer-q">
        <span>{a.question_text}</span>
        {a.required && <span className="badge outline tone-muted">required</span>}
        {a.sensitive && <span className="badge tone-warn" title="Legal, eligibility, or personal question">sensitive</span>}
      </div>
      {editing && !fileField ? (
        <div className="answer-edit">
          {input}
          {a.reuse.key ? (
            <label className="check small">
              <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
              Save for future applications <span className="muted">({a.reuse.description})</span>
            </label>
          ) : (
            <p className="small muted">Not saved for reuse: {a.reuse.description.toLowerCase()}.</p>
          )}
          <div className="row">
            <button className="primary" disabled={disabled || (save && !value)} onClick={() => onSave(value || null, save && !!a.reuse.key)}
              aria-label={`${a.verification_status === "DRAFT" ? "Confirm draft" : "Save answer"}: ${a.question_text}`}>
              {a.verification_status === "DRAFT" ? "Confirm draft" : "Save answer"}
            </button>
            {!open && <button onClick={() => setEditing(false)}>Cancel</button>}
          </div>
        </div>
      ) : (
        <div className="answer-value">
          <span>{a.answer ?? <em className="muted">blank</em>}</span>
          {!fileField && <button className="link" disabled={disabled} onClick={() => setEditing(true)} aria-label={`Edit: ${a.question_text}`}>Edit</button>}
        </div>
      )}
      <div className="small muted">
        {a.source && <>Source: {a.source}. </>}
        {a.note}
      </div>
    </div>
  );
}
