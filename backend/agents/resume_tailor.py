"""Agent 2: produce a job-specific resume using only verified facts.

Pipeline: job requirements -> draft (OpenAI, or rules without a key) -> deterministic verification ->
one repair attempt -> fall back to verbatim facts for anything still unverified -> render a new
DOCX version. The profile (master record) is never modified.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents.application_tracker import ensure_application, transition
from backend.config import get_settings
from backend.models import Job, JobScore, ResumeVersion, utcnow
from backend.models.profile import Profile
from backend.models.resume import DraftBullet, DraftEntry, RequirementMatch, TailoredDraft
from backend.models.states import Status
from backend.safety import approval
from backend.safety.resume_verifier import VerificationResult, check_bullet, quality_checks, tech_vocabulary, verify_draft
from backend.services.ats import ats_score
from backend.services.facts import FactStore, build_fact_store, verified_technologies
from backend.services.hashing import hash_file
from backend.services.llm import JobAnalysis, LLMClient, analyze_job
from backend.services.resume_render import render_docx
from backend.services.scoring import SKILL_VOCAB, _mentions, canon

log = logging.getLogger(__name__)

MAX_EXP_BULLETS = 5
MAX_PROJ_BULLETS = 3
MAX_PROJECTS = 3
TAILORABLE = {Status.SHORTLISTED, Status.RESUME_READY, Status.REVIEW_REQUIRED, Status.CHANGES_REQUESTED}
RESUME_STAGE = {Status.SHORTLISTED, Status.RESUME_READY, Status.CHANGES_REQUESTED}


class TailoringError(RuntimeError):
    pass


@dataclass
class JobRequirements:
    required: list[str]
    preferred: list[str]
    responsibilities: list[str]


@dataclass
class TailorOutcome:
    version: ResumeVersion
    report: dict


# ---- Requirements ----------------------------------------------------------------------------

def job_requirements(session: Session, job: Job, llm: LLMClient | None) -> JobRequirements:
    scores = session.scalars(select(JobScore).where(JobScore.job_id == job.id).order_by(JobScore.id.desc()))
    latest = next((s.analysis for s in scores if s.analysis), None)
    analysis: JobAnalysis | None = JobAnalysis.model_validate(latest) if latest else None
    if analysis is None and llm is not None and job.description:
        try:
            analysis = analyze_job(llm, job.title, job.company, job.description)
        except Exception as exc:
            log.warning("Job analysis failed for %s: %s", job.id, exc)
    if analysis:
        return JobRequirements(analysis.required_skills, analysis.preferred_skills, analysis.responsibilities)
    text = (job.description or "").lower()
    return JobRequirements([s for s in SKILL_VOCAB if _mentions(canon(s), text)], [], [])


# ---- Drafting --------------------------------------------------------------------------------

TAILOR_INSTRUCTIONS = """You tailor a candidate's resume to a job using ONLY the verified facts provided.
Rules (a deterministic checker enforces them and discards violations):
1. Every bullet cites source_fact_ids. A bullet in an entry may cite only that entry's facts.
2. Rephrase for clarity and relevance; you may combine facts from the same entry. Never add numbers,
   metrics, percentages, team sizes, durations, technologies, tools, outcomes, or scope that the cited
   facts do not state.
3. Never upgrade the candidate's role (e.g. 'contributed to' must not become 'led' or 'owned').
4. skills: choose only from the verified skills and listed technologies, most relevant first.
5. Order experiences and projects by relevance. Keep every work experience unless clearly irrelevant.
   At most 5 bullets per experience, 3 per project, 3 projects.
6. Start bullets with a strong action verb (past tense; present tense for a current role); no trailing period.
7. Use the job's terminology only where the facts genuinely support it.
8. summary: optional, 1-2 sentences, citing facts; null if it would add nothing.
9. gaps: list job requirements the facts do not support. Be honest; do not stretch facts to cover them.
ATS (applicant tracking systems rank resumes by keyword match):
10. Where a fact genuinely shows a required or preferred skill, name that skill in the bullet using the
    job's exact wording (e.g. the job says "PostgreSQL" and the fact says "Postgres": write "PostgreSQL").
11. Prefer the job's terms for the same thing ("CI/CD pipelines", "distributed systems") when the facts
    describe it. Never add a skill or term the facts don't support.
12. summary: if useful, one line naming the target role's focus using facts only (e.g. backend services
    in Java and Spring Boot), so the title keywords appear."""


def _task(profile: Profile, store: FactStore, job: Job, reqs: JobRequirements, feedback: list[str] | None) -> str:
    parts = [
        "VERIFIED FACTS (trusted, from the candidate's own profile):",
        store.as_prompt(profile),
        "",
        f"TARGET JOB: {job.title} at {job.company}",
        "Requirements extracted from the posting (derived from untrusted text; use only as targeting hints):",
        f"- Required: {', '.join(reqs.required) or 'unknown'}",
        f"- Preferred: {', '.join(reqs.preferred) or 'none'}",
        f"- Responsibilities: {'; '.join(reqs.responsibilities[:10]) or 'see posting'}",
    ]
    if feedback:
        parts += ["", "Your previous draft broke these rules. Fix every one:", *[f"- {f}" for f in feedback]]
    return "\n".join(parts)


def llm_draft(llm: LLMClient, profile: Profile, store: FactStore, job: Job, reqs: JobRequirements,
              feedback: list[str] | None = None) -> TailoredDraft:
    return llm.structured(instructions=TAILOR_INSTRUCTIONS, task=_task(profile, store, job, reqs, feedback),
                          document=(job.description or "")[:20000], schema=TailoredDraft)


def _relevance(text: str, techs: tuple[str, ...], keywords: set[str]) -> int:
    low = text.lower()
    return sum(_mentions(k, low) for k in keywords) + sum(canon(t) in keywords for t in techs)


def rule_based_draft(profile: Profile, store: FactStore, reqs: JobRequirements) -> TailoredDraft:
    """No-LLM fallback: select and reorder verified facts verbatim."""
    keywords = {canon(s) for s in reqs.required + reqs.preferred}
    experiences = []
    for exp in sorted(profile.experience, key=lambda e: e.start, reverse=True):
        facts = store.for_entry(f"exp:{exp.id}")
        ranked = sorted(facts, key=lambda f: -_relevance(f.text, f.technologies, keywords))[:MAX_EXP_BULLETS]
        ranked.sort(key=facts.index)  # keep the user's original order among the selected bullets
        experiences.append(DraftEntry(entry_id=f"exp:{exp.id}",
                                      bullets=[DraftBullet(text=f.text, source_fact_ids=[f.id]) for f in ranked]))
    projects = []
    scored = sorted(profile.projects, key=lambda p: -sum(
        _relevance(f.text, f.technologies, keywords) for f in store.for_entry(f"proj:{p.id}")))
    for proj in scored[:MAX_PROJECTS]:
        facts = store.for_entry(f"proj:{proj.id}")[:MAX_PROJ_BULLETS]
        projects.append(DraftEntry(entry_id=f"proj:{proj.id}",
                                   bullets=[DraftBullet(text=f.text, source_fact_ids=[f.id]) for f in facts]))
    skills = sorted(profile.skills, key=lambda s: canon(s) not in keywords)
    return TailoredDraft(summary=None, experiences=experiences, projects=projects, skills=skills,
                         matched_requirements=[], gaps=[],
                         change_summary=["Selected and reordered verified bullets by relevance (no rewording)"])


# ---- Enforcement -----------------------------------------------------------------------------

def sanitize(draft: TailoredDraft, profile: Profile, store: FactStore, job_skills: list[str]) -> tuple[TailoredDraft, list[str]]:
    """Replace anything still unverified with verbatim source facts (or drop it)."""
    corrections: list[str] = []
    vocab = tech_vocabulary(profile, job_skills)
    known = {f"exp:{e.id}" for e in profile.experience} | {f"proj:{p.id}" for p in profile.projects}

    def clean_bullet(b: DraftBullet, where: str, entry_id: str | None) -> DraftBullet | None:
        r = VerificationResult()
        check_bullet(b, where, entry_id, store, vocab, r)
        if r.ok:
            return b
        valid = [store.get(f) for f in b.source_fact_ids if store.get(f)
                 and (entry_id is None or store.get(f).entry_id == entry_id)]
        valid = [f for f in valid if f.kind != "skill"]
        reasons = "; ".join(i.message for i in r.violations)
        if entry_id is None or not valid:
            corrections.append(f"Removed {where} ({reasons})")
            return None
        corrections.append(f"Replaced {where} with your original wording ({reasons})")
        return DraftBullet(text=valid[0].text, source_fact_ids=[valid[0].id])

    def clean_entries(entries: list[DraftEntry], prefix: str, limit: int) -> list[DraftEntry]:
        out, seen = [], set()
        for e in entries:
            if e.entry_id not in known or not e.entry_id.startswith(prefix) or e.entry_id in seen:
                corrections.append(f"Removed unknown or repeated entry '{e.entry_id}'")
                continue
            seen.add(e.entry_id)
            bullets, texts = [], set()
            for i, b in enumerate(e.bullets[:limit]):
                cleaned = clean_bullet(b, f"{e.entry_id} bullet {i + 1}", e.entry_id)
                if cleaned and cleaned.text not in texts:
                    bullets.append(cleaned)
                    texts.add(cleaned.text)
            out.append(DraftEntry(entry_id=e.entry_id, bullets=bullets))
        return out

    verified = {canon(s) for s in profile.skills} | verified_technologies(profile)
    skills = []
    for s in draft.skills:
        if canon(s) in verified and canon(s) not in {canon(x) for x in skills}:
            skills.append(s)
        elif canon(s) not in verified:
            corrections.append(f"Removed unverified skill '{s}'")

    matched = [RequirementMatch(requirement=m.requirement, fact_ids=[f for f in m.fact_ids if store.get(f)])
               for m in draft.matched_requirements]
    clean = TailoredDraft(
        summary=clean_bullet(draft.summary, "summary", None) if draft.summary else None,
        experiences=clean_entries(draft.experiences, "exp:", MAX_EXP_BULLETS),
        projects=clean_entries(draft.projects, "proj:", MAX_PROJ_BULLETS)[:MAX_PROJECTS],
        skills=skills,
        matched_requirements=[m for m in matched if m.fact_ids],
        gaps=draft.gaps,
        change_summary=draft.change_summary,
    )
    return clean, corrections


def ensure_all_experience(draft: TailoredDraft, profile: Profile, store: FactStore,
                          reqs: JobRequirements, per_job: int = 3) -> tuple[TailoredDraft, list[str]]:
    """Every job in the profile appears, newest first. Leaving jobs out shrinks your apparent years of
    experience and leaves gaps screeners notice. A job the model skipped gets its most relevant
    bullets in your original wording."""
    keywords = {canon(s) for s in reqs.required + reqs.preferred}
    present = {e.entry_id for e in draft.experiences}
    entries = list(draft.experiences)
    notes = []
    for exp in profile.experience:
        entry_id = f"exp:{exp.id}"
        if entry_id in present:
            continue
        facts = store.for_entry(entry_id)
        ranked = sorted(facts, key=lambda f: -_relevance(f.text, f.technologies, keywords))[:per_job]
        ranked.sort(key=facts.index)
        entries.append(DraftEntry(entry_id=entry_id,
                                  bullets=[DraftBullet(text=f.text, source_fact_ids=[f.id]) for f in ranked]))
        notes.append(f"Added {exp.title} at {exp.organization}, which the draft left out (your original wording)")
    # Current roles first, then by most recent end date, then start date.
    when = {f"exp:{e.id}": (e.end or "9999-12", e.start) for e in profile.experience}
    entries.sort(key=lambda e: when.get(e.entry_id, ("", "")), reverse=True)
    return draft.model_copy(update={"experiences": entries}), notes


def ats_optimize(draft: TailoredDraft, profile: Profile, reqs: JobRequirements) -> tuple[TailoredDraft, list[str]]:
    """Deterministic ATS pass over a verified draft: job spelling for skills you have, every matching
    required/preferred skill present, job skills first. Never adds an unverified skill."""
    verified = {canon(s) for s in profile.skills} | verified_technologies(profile)
    job_spelling = {canon(s): s for s in reqs.preferred + reqs.required}  # required spelling wins
    notes: list[str] = []
    skills: list[str] = []
    for s in draft.skills:
        name = job_spelling.get(canon(s), s)
        if name != s:
            notes.append(f"Listed '{s}' as '{name}' to match the job's wording")
        if canon(name) not in {canon(x) for x in skills}:
            skills.append(name)
    for s in reqs.required + reqs.preferred:
        if canon(s) in verified and canon(s) not in {canon(x) for x in skills}:
            skills.append(s)
            notes.append(f"Added '{s}' to skills: the job asks for it and it's in your verified profile")
    rank = {canon(s): i for i, s in enumerate(reqs.required + reqs.preferred)}
    skills.sort(key=lambda s: rank.get(canon(s), len(rank)))
    return draft.model_copy(update={"skills": skills}), notes


def _deterministic_matches(profile: Profile, store: FactStore, reqs: JobRequirements) -> tuple[list[dict], list[str]]:
    verified = verified_technologies(profile)
    matched, gaps = [], []
    for skill in reqs.required:
        c = canon(skill)
        ids = [f.id for f in store.facts.values()
               if f.kind != "skill" and (_mentions(c, f.text.lower()) or c in {canon(t) for t in f.technologies})]
        if c in verified:
            matched.append({"requirement": skill, "fact_ids": ids[:5] or [f"skill:{skill}"]})
        else:
            gaps.append(skill)
    return matched, gaps


# ---- Orchestration ---------------------------------------------------------------------------

def tailor_resume(session: Session, job: Job, profile: Profile, llm: LLMClient | None, *,
                  standalone: bool = False) -> TailorOutcome:
    """standalone: the user asked for a resume for this job directly (e.g. a pasted description). The
    resume is generated even while the job waits on an eligibility review, without moving its status."""
    app = ensure_application(session, job)
    status = Status(app.status)
    in_resume_stage = status in TAILORABLE and not (
        status == Status.REVIEW_REQUIRED and app.previous_status not in RESUME_STAGE)
    if not in_resume_stage and not (standalone and status in (Status.DISCOVERED, Status.REVIEW_REQUIRED, Status.EXCLUDED)):
        if status == Status.REVIEW_REQUIRED:
            # The open review is about eligibility, duplicates, or freshness; tailoring must not clear it.
            raise TailoringError("Resolve the open review first (e.g. move the job to SHORTLISTED)")
        raise TailoringError(f"Cannot tailor a resume while the application is {status}")
    if not profile.experience and not profile.projects:
        raise TailoringError("Profile has no experience or projects to build a resume from")

    store = build_fact_store(profile)
    reqs = job_requirements(session, job, llm)
    job_skills = reqs.required + reqs.preferred
    method, attempts, first_violations = "rules", 0, []

    draft: TailoredDraft | None = None
    if llm is not None:
        feedback = None
        for attempts in (1, 2):
            try:
                candidate = llm_draft(llm, profile, store, job, reqs, feedback)
            except Exception as exc:
                log.warning("LLM tailoring failed for job %s: %s", job.id, exc)
                break
            draft, method = candidate, "openai"
            result = verify_draft(candidate, profile, store, job_skills)
            if result.ok:
                break
            first_violations = first_violations or [str(v) for v in result.violations]
            feedback = [str(v) for v in result.violations]
    if draft is None:
        draft, method = rule_based_draft(profile, store, reqs), "rules"

    draft, corrections = sanitize(draft, profile, store, job_skills)
    final = verify_draft(draft, profile, store, job_skills)
    if not final.ok:  # sanitize guarantees this; refuse rather than ship an unverified claim
        raise TailoringError(f"Draft failed verification: {[str(v) for v in final.violations]}")

    draft, history_notes = ensure_all_experience(draft, profile, store, reqs)
    draft, ats_notes = ats_optimize(draft, profile, reqs)
    ats_notes = history_notes + ats_notes
    final = verify_draft(draft, profile, store, job_skills)
    if not final.ok:
        raise TailoringError(f"ATS pass produced an unverified claim: {[str(v) for v in final.violations]}")
    matched, gaps = _deterministic_matches(profile, store, reqs)
    report = {
        "method": method,
        "model": llm.model if method == "openai" else None,
        "llm_attempts": attempts,
        "change_summary": draft.change_summary,
        "matched_requirements": [m.model_dump() for m in draft.matched_requirements] or matched,
        "gaps": sorted(set(gaps) | set(draft.gaps)),
        "claims_requiring_review": [str(c) for c in final.review_claims],
        "auto_corrections": corrections,
        "initial_violations": first_violations,
        "quality": quality_checks(profile, draft, reqs.required),
        "ats": ats_score(draft, profile, title=job.title, description=job.description or "",
                         required=reqs.required, preferred=reqs.preferred),
        "ats_changes": ats_notes,
    }
    cited = sorted({f for e in draft.experiences + draft.projects for b in e.bullets for f in b.source_fact_ids}
                   | (set(draft.summary.source_fact_ids) if draft.summary else set()))
    report["cited_facts"] = {f: store.get(f).text for f in cited}  # snapshot for review and audit
    coursework = None
    if profile.education:
        kw = {canon(s) for s in job_skills} | {w.lower() for r in reqs.responsibilities for w in r.split() if len(w) > 4}
        coursework = sorted(profile.education[0].coursework, key=lambda c: -sum(k in c.lower() for k in kw))

    number = (session.scalar(select(func.count()).where(ResumeVersion.job_id == job.id)) or 0) + 1
    out_dir = get_settings().data_dir / "resumes" / f"job_{job.id}"
    path = render_docx(profile, draft, out_dir / f"resume_v{number}.docx", coursework)
    sidecar = path.with_suffix(".json")
    sidecar.write_text(json.dumps({
        "job": {"id": job.id, "company": job.company, "title": job.title, "url": job.url},
        "created_at": utcnow().isoformat(),
        "draft": draft.model_dump(),
        "report": report,
    }, indent=2))
    sidecar.chmod(0o600)

    version = ResumeVersion(job_id=job.id, version=number, file_path=str(path), content_hash=hash_file(path),
                            content=draft.model_dump(), report=report, review_status="PENDING")
    session.add(version)
    session.flush()
    approval.on_content_changed(session, app, resume_version_id=version.id)

    needs_review = bool(final.review_claims)
    detail = f"Resume v{number} ({method})"
    if not in_resume_stage:
        pass  # standalone resume for a job outside the resume stage: its status is left alone
    elif status in (Status.SHORTLISTED, Status.CHANGES_REQUESTED, Status.RESUME_READY) and needs_review:
        transition(session, app, Status.REVIEW_REQUIRED, reason=f"{detail}: {len(final.review_claims)} claim(s) need review")
    elif status != Status.RESUME_READY and not needs_review:
        transition(session, app, Status.RESUME_READY, reason=f"{detail} generated and verified")
    session.commit()
    return TailorOutcome(version=version, report=report)


def review_resume(session: Session, version: ResumeVersion, *, approve: bool, note: str = "") -> ResumeVersion:
    """User decision on a resume version. Approval accepts any flagged claims as accurate."""
    job = session.get(Job, version.job_id)
    app = ensure_application(session, job)
    if app.resume_version_id != version.id:
        raise TailoringError("Only the application's current resume version can be reviewed")
    version.review_status = "APPROVED" if approve else "REJECTED"
    version.approved_at = utcnow() if approve else None
    status = Status(app.status)
    resume_review = status == Status.REVIEW_REQUIRED and app.previous_status in RESUME_STAGE
    if approve and resume_review:
        transition(session, app, Status.RESUME_READY, reason=f"User approved resume v{version.version}. {note}".strip())
    elif not approve and status == Status.RESUME_READY:
        transition(session, app, Status.REVIEW_REQUIRED, reason=f"User rejected resume v{version.version}. {note}".strip())
    session.commit()
    return version
