import type { AtsScore } from "../api/types";

const LABELS = {
  required: "Required skills",
  preferred: "Preferred skills",
  title: "Job title keywords",
  terms: "Key terms from the posting",
  format: "ATS-friendly format",
} as const;

export function AtsCard({ ats, changes }: { ats: AtsScore; changes: string[] }) {
  const tone = ats.score >= 80 ? "good" : ats.score >= 60 ? "info" : "warn";
  const capped = ats.required.missing_not_in_profile.length + ats.preferred.missing_not_in_profile.length;
  return (
    <section className="card">
      <h2>ATS match</h2>
      <div className="ats-head">
        <span className={`ats-score tone-text-${tone}`}>{ats.score}</span>
        <span className="muted">/ 100</span>
      </div>
      <ul className="ats-bars">
        {(Object.keys(LABELS) as (keyof typeof LABELS)[]).map((k) => (
          <li key={k}>
            <span className="small">{LABELS[k]}</span>
            <span className="score-track wide"><span className="score-fill" style={{ width: `${(ats.points[k] / ats.weights[k]) * 100}%` }} /></span>
            <span className="small muted nowrap">{ats.points[k]} / {ats.weights[k]}</span>
          </li>
        ))}
      </ul>

      {capped > 0 && (
        <p className="small">
          The job asks for {capped} skill{capped === 1 ? "" : "s"} that aren't in your profile, which caps the skills score.
          Add one to your profile only if you've really used it, and the next version can include it.
        </p>
      )}
      <h3>Skills</h3>
      <div className="chips">
        {[...ats.required.found, ...ats.preferred.found].map((s) => <span key={`f-${s}`} className="chip tone-good">✓ {s}</span>)}
        {[...ats.required.missing_you_have, ...ats.preferred.missing_you_have].map((s) => (
          <span key={`h-${s}`} className="chip tone-warn" title="In your profile but not on this resume">! {s}</span>
        ))}
        {[...ats.required.missing_not_in_profile, ...ats.preferred.missing_not_in_profile].map((s) => (
          <span key={`m-${s}`} className="chip tone-muted" title="Not in your profile, so not claimed">✗ {s}</span>
        ))}
      </div>
      {ats.soft_skills && ats.soft_skills.length > 0 && (
        <p className="small muted">
          Also asked for: {ats.soft_skills.join(", ")}. ATS keyword matching doesn't score these reliably, so they aren't
          counted. Be ready to show them with examples in interviews.
        </p>
      )}
      {ats.job_terms.terms.length > 0 && (
        <>
          <h3>Key terms from the posting</h3>
          <div className="chips">
            {ats.job_terms.terms.map((t) => (
              <span key={t} className={`chip ${ats.job_terms.found.includes(t) ? "tone-good" : "tone-muted"}`}>
                {ats.job_terms.found.includes(t) ? "✓" : "✗"} {t}
              </span>
            ))}
          </div>
        </>
      )}
      <h3>Format</h3>
      <ul className="plain small">
        {Object.entries(ats.format).map(([k, ok]) => <li key={k} className={ok ? "tone-text-good" : "tone-text-warn"}>{ok ? "✓" : "!"} {k}</li>)}
      </ul>
      {changes.length > 0 && (
        <>
          <h3>ATS adjustments made</h3>
          <ul className="plain small">{changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </>
      )}
    </section>
  );
}
