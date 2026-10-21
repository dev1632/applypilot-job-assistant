"""Agent 1: collect, normalize, deduplicate, score, and screen job postings."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.agents.application_tracker import ensure_application, transition
from backend.config import get_settings
from backend.models import Job, JobScore, utcnow
from backend.models.profile import Profile
from backend.models.states import Status
from backend.safety.eligibility import Outcome, screen
from backend.services.llm import JobAnalysis, LLMClient, analyze_job
from backend.services.normalize import job_fingerprint, normalize_url
from backend.services.scoring import role_family_for, score_fit
from backend.sources import RawPosting, SourceAdapter, build_adapter

log = logging.getLogger(__name__)


@dataclass
class ScanReport:
    fetched: int = 0
    skipped_role: int = 0
    duplicates: int = 0
    new_jobs: int = 0
    shortlisted: int = 0
    excluded: int = 0
    review: int = 0
    errors: list[str] = field(default_factory=list)


def is_recent(job: Job, max_age_hours: int) -> bool | None:
    """True/False when we have date evidence; None when freshness is unverified."""
    if job.posted_at is None or job.freshness_confidence == "UNVERIFIED":
        return None
    posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
    return utcnow() - posted <= timedelta(hours=max_age_hours)


def _title_wanted(profile: Profile, title: str) -> bool:
    t = title.lower()
    if any(x in t for x in profile.preferences.title_exclude):
        return False
    return role_family_for(title, profile.preferences.role_families) is not None


def find_existing(session: Session, raw: RawPosting, norm_url: str) -> Job | None:
    conds = [Job.normalized_url == norm_url]
    if raw.source_job_id:
        conds.append((Job.source == raw.source) & (Job.source_job_id == raw.source_job_id))
    return session.scalar(select(Job).where(or_(*conds)))


def upsert_posting(session: Session, raw: RawPosting) -> tuple[Job, bool]:
    """Insert a posting unless it already exists. Returns (job, created)."""
    norm = normalize_url(raw.url)
    existing = find_existing(session, raw, norm)
    if existing:
        # Keep the earliest evidenced posting date; never overwrite verified with unverified.
        if raw.posted_at and existing.freshness_confidence == "UNVERIFIED" and raw.freshness_confidence != "UNVERIFIED":
            existing.posted_at, existing.freshness_confidence = raw.posted_at, raw.freshness_confidence
        return existing, False

    fp = job_fingerprint(raw.company, raw.title, raw.location)
    twin = session.scalar(select(Job).where(Job.fingerprint == fp))
    if twin is None and raw.requisition_id:
        twin = session.scalar(select(Job).where(Job.company == raw.company, Job.requisition_id == raw.requisition_id))
    job = Job(
        source=raw.source, source_job_id=raw.source_job_id, company=raw.company, title=raw.title,
        location=raw.location, employment_type=raw.employment_type, url=raw.url, normalized_url=norm,
        requisition_id=raw.requisition_id, description=raw.description, posted_at=raw.posted_at,
        freshness_confidence=raw.freshness_confidence, fingerprint=fp,
        possible_duplicate_of=twin.id if twin else None,
    )
    session.add(job)
    session.flush()
    return job, True


def evaluate(session: Session, job: Job, profile: Profile, llm: LLMClient | None) -> JobScore:
    analysis: JobAnalysis | None = None
    if llm is not None and job.description:
        try:
            analysis = analyze_job(llm, job.title, job.company, job.description)
        except Exception as exc:  # LLM is an enhancement; fall back to rules on any failure
            log.warning("LLM analysis failed for job %s: %s", job.id, exc)
    fit = score_fit(profile, title=job.title, description=job.description or "", analysis=analysis)
    elig = screen(profile, title=job.title, description=job.description or "", location=job.location,
                  employment_type=job.employment_type, analysis=analysis)
    score = JobScore(job_id=job.id, skills_score=fit.score, eligibility_status=elig.outcome,
                     match_reasons=fit.reasons, gaps=fit.gaps, eligibility_notes=elig.notes,
                     analysis=analysis.model_dump() if analysis else None)
    session.add(score)
    session.flush()
    return score


def triage(session: Session, job: Job, score: JobScore, profile: Profile, report: ScanReport) -> None:
    settings = get_settings()
    app = ensure_application(session, job)
    if app.status != Status.DISCOVERED:
        return
    recent = is_recent(job, settings.applypilot_max_posting_age_hours)

    if job.possible_duplicate_of:
        transition(session, app, Status.REVIEW_REQUIRED, reason=f"Possible duplicate of job {job.possible_duplicate_of}")
        report.review += 1
    elif score.eligibility_status == Outcome.FAIL:
        transition(session, app, Status.EXCLUDED, reason="; ".join(n for n in score.eligibility_notes if n.startswith("FAIL")))
        report.excluded += 1
    elif recent is False:
        transition(session, app, Status.EXCLUDED, reason=f"Posted more than {settings.applypilot_max_posting_age_hours}h ago")
        report.excluded += 1
    elif score.skills_score < profile.preferences.min_skills_score:
        transition(session, app, Status.EXCLUDED, reason=f"Fit score {score.skills_score} below threshold")
        report.excluded += 1
    elif score.eligibility_status == Outcome.REVIEW or (recent is None and settings.applypilot_require_verified_freshness):
        notes = [n for n in score.eligibility_notes if n.startswith("REVIEW")] or ["Posting date is unverified"]
        transition(session, app, Status.REVIEW_REQUIRED, reason="; ".join(notes))
        report.review += 1
    else:
        transition(session, app, Status.SHORTLISTED, reason=f"Eligibility PASS, fit {score.skills_score}")
        report.shortlisted += 1


def run_scan(session: Session, profile: Profile, *, adapters: list[SourceAdapter] | None = None,
             llm: LLMClient | None = None) -> ScanReport:
    report = ScanReport()
    if adapters is None:
        adapters = []
        for cfg in profile.preferences.sources:
            try:
                adapters.append(build_adapter(cfg))
            except ValueError as exc:
                report.errors.append(str(exc))

    for adapter in adapters:
        try:
            postings = adapter.fetch()
        except Exception as exc:
            report.errors.append(f"{adapter.name}:{adapter.board}: {exc}")
            continue
        report.fetched += len(postings)
        for raw in postings:
            if not _title_wanted(profile, raw.title):
                report.skipped_role += 1
                continue
            job, created = upsert_posting(session, raw)
            if not created:
                report.duplicates += 1
                continue
            report.new_jobs += 1
            # Cheap checks first: a posting already too old to apply to doesn't get an OpenAI analysis.
            stale = is_recent(job, get_settings().applypilot_max_posting_age_hours) is False
            score = evaluate(session, job, profile, None if stale else llm)
            triage(session, job, score, profile, report)
        session.commit()
    return report
