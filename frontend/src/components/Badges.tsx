import { humanStatus } from "./format";

const STATUS_TONE: Record<string, string> = {
  SHORTLISTED: "info",
  RESUME_READY: "good",
  APPLICATION_PREPARED: "info",
  AWAITING_APPROVAL: "warn",
  APPROVED: "good",
  SUBMITTED: "good",
  INTERVIEW: "good",
  OFFER: "good",
  REVIEW_REQUIRED: "warn",
  CHANGES_REQUESTED: "warn",
  SUBMISSION_UNKNOWN: "warn",
  MANUAL_VERIFICATION: "warn",
  EXCLUDED: "muted",
  CANCELLED: "muted",
  WITHDRAWN: "muted",
  REJECTED: "muted",
  INVALIDATED: "bad",
  FAILED: "bad",
};

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  return <span className={`badge tone-${STATUS_TONE[status] ?? "muted"}`}>{humanStatus(status)}</span>;
}

export function EligibilityBadge({ value }: { value: string | undefined }) {
  if (!value) return null;
  const tone = value === "PASS" ? "good" : value === "FAIL" ? "bad" : "warn";
  return <span className={`badge tone-${tone}`}>{value}</span>;
}

const FRESHNESS_HELP: Record<string, string> = {
  VERIFIED: "The source states when this was published",
  ESTIMATED: "Based on the posting's creation time, which approximates publication",
  UNVERIFIED: "The source gives no reliable publication date",
};

export function FreshnessBadge({ value }: { value: string }) {
  const tone = value === "VERIFIED" ? "good" : value === "ESTIMATED" ? "info" : "warn";
  return (
    <span className={`badge outline tone-${tone}`} title={FRESHNESS_HELP[value]}>
      {value.toLowerCase()}
    </span>
  );
}

export function ScoreBar({ score }: { score: number | undefined }) {
  if (score === undefined) return <span className="muted">—</span>;
  const pct = Math.round(score * 100);
  return (
    <span className="score" title={`Fit score ${pct}%`}>
      <span className="score-track">
        <span className="score-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="score-num">{pct}</span>
    </span>
  );
}
