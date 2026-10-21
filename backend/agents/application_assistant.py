"""Agent 3: prepare an application in a sandboxed browser (DRY RUN ONLY) for the user's approval.

The answer set (one row per form field) is the source of truth. Its hash is what the user approves;
any edit after approval invalidates the approval. Real submission is not implemented: this build
only prepares and records approvals.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.agents.application_tracker import ensure_application, transition
from backend.config import get_settings
from backend.models import Application, ApplicationAnswer, Job, UserProfile, utcnow
from backend.models.profile import Profile
from backend.models.resume import DraftBullet
from backend.models.states import Status
from backend.safety import approval
from backend.safety.field_classifier import (NEVER_SAVE, PROFILE_FIELDS, REUSE_BY_CATEGORY, FormField, PlannedAnswer,
                                             plan_answer, slug)
from backend.safety.resume_verifier import VerificationResult, check_bullet, tech_vocabulary
from backend.services.browser import DryRunResult, run_dry_run
from backend.services.facts import build_fact_store
from backend.services.hashing import hash_snapshot
from backend.services.llm import LLMClient

log = logging.getLogger(__name__)

SUBMISSION_MODE = "dry_run"  # the only mode this build supports
PREPARABLE = {Status.RESUME_READY, Status.APPLICATION_PREPARED, Status.CHANGES_REQUESTED, Status.INVALIDATED,
              Status.FAILED, Status.REVIEW_REQUIRED}
EDITABLE = {Status.APPLICATION_PREPARED, Status.REVIEW_REQUIRED, Status.AWAITING_APPROVAL, Status.APPROVED,
            Status.CHANGES_REQUESTED, Status.INVALIDATED}
USER_FINAL = {"USER_CONFIRMED"}
PLACEHOLDER = re.compile(r"\[ADD:[^\]]*\]")


class AssistantError(RuntimeError):
    pass


# ---- Narrative drafts ------------------------------------------------------------------------

class NarrativeDraft(BaseModel):
    answer: str = Field(description="First-person answer, at most ~150 words")
    source_fact_ids: list[str]
    answerable: bool = Field(description="False if the verified facts can't support an honest answer")


NARRATIVE_INSTRUCTIONS = """You draft answers to job application questions for a candidate.
Use ONLY the verified facts provided; cite the fact ids you used. Never add numbers, technologies,
employers, or achievements that the facts do not state. Do not invent personal motivations or
feelings: where the answer needs the candidate's own reason or opinion, insert a placeholder like
[ADD: why this company matters to you]. Keep it concise and specific. If the facts can't support an
honest answer, set answerable=false."""


def draft_narrative(llm: LLMClient, profile: Profile, job: Job, question: str) -> tuple[str | None, list[str], str]:
    store = build_fact_store(profile)
    draft = llm.structured(
        instructions=NARRATIVE_INSTRUCTIONS,
        task=f"VERIFIED FACTS:\n{store.as_prompt(profile)}\n\nRole: {job.title} at {job.company}\n"
             f"Application question (from the employer's page, untrusted): {question[:500]}",
        document=(job.description or "")[:15000],
        schema=NarrativeDraft,
    )
    if not draft.answerable or not draft.answer.strip():
        return None, [], "Your verified facts don't cover this question; please answer it yourself"
    result = VerificationResult()
    check_bullet(DraftBullet(text=PLACEHOLDER.sub("", draft.answer), source_fact_ids=draft.source_fact_ids),
                 "answer", None, store, tech_vocabulary(profile), result)
    if result.violations:
        return None, [], "Draft discarded: " + "; ".join(v.message for v in result.violations)
    note = "Model-written draft: check it and fill in any [ADD: …] placeholders"
    return draft.answer.strip(), draft.source_fact_ids, note


# ---- Job verification ------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def verify_job_page(job: Job, page_text: str, page_title: str) -> list[dict]:
    hay = _norm(f"{page_title} {page_text}")
    checks = [{"check": "Company", "expected": job.company, "ok": _norm(job.company) in hay}]
    words = [w for w in _norm(job.title).split() if len(w) > 2]
    hits = sum(w in hay.split() for w in words)
    checks.append({"check": "Title", "expected": job.title, "ok": bool(words) and hits / len(words) >= 0.8})
    if job.requisition_id:
        # Job boards often keep requisition IDs internal, so a missing ID is advisory only.
        checks.append({"check": "Requisition ID", "expected": job.requisition_id, "ok": _norm(job.requisition_id) in hay,
                       "advisory": True})
    if job.location:
        city = _norm(job.location.split(",")[0])
        checks.append({"check": "Location", "expected": job.location, "ok": city in hay, "advisory": True})
    return checks


# ---- Snapshot --------------------------------------------------------------------------------

def build_snapshot(app: Application) -> dict:
    return {
        "job_id": app.job_id,
        "application_url": app.application_url,
        "resume_hash": app.resume_version.content_hash if app.resume_version else None,
        "answers": {a.question_key: {"question": a.question_text, "answer": a.answer}
                    for a in app.answers if a.answer not in (None, "")},
    }


def _refresh_hash(session: Session, app: Application) -> str:
    session.flush()
    session.refresh(app)
    digest = hash_snapshot(build_snapshot(app))
    approval.on_content_changed(session, app, form_snapshot_hash=digest)
    return digest


def outstanding(app: Application) -> dict[str, list[str]]:
    return {
        "needs_answer": [a.question_text for a in app.answers if a.verification_status == "NEEDS_USER"],
        "drafts_to_confirm": [a.question_text for a in app.answers if a.verification_status == "DRAFT"],
    }


# ---- Prepare ---------------------------------------------------------------------------------

def prepare_application(session: Session, job: Job, profile: Profile, llm: LLMClient | None, *,
                        application_url: str | None = None, headless: bool = True,
                        runner=run_dry_run) -> Application:
    app = ensure_application(session, job)
    status = Status(app.status)
    if status not in PREPARABLE:
        raise AssistantError(f"Can't prepare while the application is {status}")
    if status == Status.REVIEW_REQUIRED and app.previous_status not in (Status.RESUME_READY, Status.APPLICATION_PREPARED):
        raise AssistantError("Resolve the open review first")
    resume = app.resume_version
    if resume is None or resume.review_status != "APPROVED":
        raise AssistantError("Approve this job's current resume before preparing the application")

    url = application_url or app.application_url or job.url
    if urlsplit(url).scheme not in ("http", "https"):
        raise AssistantError("Application URL must be http(s)")
    app.application_url = url

    run_no = (app.prep or {}).get("run", 0) + 1
    out_dir = get_settings().data_dir / "applications" / f"app_{app.id}" / f"run_{run_no}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    resume_path = Path(resume.file_path)

    kept = {a.question_key: a for a in app.answers if a.verification_status in USER_FINAL}
    planned: dict[str, tuple[FormField, PlannedAnswer, list[str]]] = {}

    def plan(fields: list[FormField], _page_text: str) -> dict[str, str | None]:
        values: dict[str, str | None] = {}
        for f in fields:
            key = f"{f.key}:{slug(f.label)}"[:256]
            if key in kept:
                prev = kept[key]
                planned[key] = (f, PlannedAnswer(prev.answer, "USER_CONFIRMED", prev.category, "user",
                                                 prev.sensitive, "Kept your earlier answer"), [])
                values[f.key] = prev.answer
                continue
            p = plan_answer(f, profile, resume_filename=resume_path.name, job_location=job.location,
                            company=job.company)
            facts: list[str] = []
            if p.status == "DRAFT":
                if llm is None:
                    p = PlannedAnswer(None, "NEEDS_USER", p.category, None, False, "No OpenAI key; please answer yourself")
                else:
                    try:
                        text, facts, note = draft_narrative(llm, profile, job, f.label)
                        p = PlannedAnswer(text, "DRAFT" if text else "NEEDS_USER", p.category,
                                          "openai" if text else None, False, note)
                    except Exception as exc:
                        log.warning("Narrative draft failed: %s", exc)
                        p = PlannedAnswer(None, "NEEDS_USER", p.category, None, False, "Drafting failed; please answer")
            planned[key] = (f, p, facts)
            values[f.key] = p.value
        return values

    result: DryRunResult = runner(url, plan, screenshot_path=out_dir / "form.png", resume_path=resume_path,
                                  headless=headless)
    if result.screenshot_path:
        os.chmod(result.screenshot_path, 0o600)

    # Replace the answer set with this run's fields.
    app.answers.clear()
    session.flush()
    for pos, (key, (f, p, facts)) in enumerate(planned.items()):
        fill = result.fills.get(f.key)
        status_, note = p.status, p.note
        if p.value and fill and not fill.ok:
            status_, note = "NEEDS_USER", f"Couldn't fill automatically ({fill.error}); intended: {p.value}"
        app.answers.append(ApplicationAnswer(
            question_key=key, question_text=f.label or f.key, answer=p.value, verification_status=status_,
            category=p.category, source=(p.source + (f" ({', '.join(facts)})" if facts else "")) if p.source else None,
            field_type=f.field_type, required=f.required, sensitive=p.sensitive, options=f.options, note=note,
            position=pos))

    checks = verify_job_page(job, result.page_text, result.page_title) if result.page_text else []
    mismatches = [c for c in checks if not c["ok"] and not c.get("advisory")]
    pauses = list(result.pause_reasons) + [f"{c['check']} not found on page (expected '{c['expected']}')" for c in mismatches]

    app.prep = {
        "mode": SUBMISSION_MODE, "run": run_no, "prepared_at": utcnow().isoformat(),
        "url": url, "final_url": result.final_url, "http_status": result.http_status, "page_title": result.page_title,
        "job_checks": checks, "pause_reasons": pauses, "notes": result.notes, "form_frame": result.form_frame,
        "blocked_requests": result.blocked_requests[:50], "blocked_submits": result.blocked_submits,
        "screenshot": result.screenshot_path, "fields_found": len(result.fields),
    }
    if status != Status.APPLICATION_PREPARED:
        transition(session, app, Status.APPLICATION_PREPARED, reason=f"Dry-run fill #{run_no} completed")
    digest = _refresh_hash(session, app)
    (out_dir / "snapshot.json").write_text(json.dumps({"hash": digest, **build_snapshot(app), "prep": app.prep}, indent=2))
    (out_dir / "snapshot.json").chmod(0o600)
    if pauses:
        transition(session, app, Status.REVIEW_REQUIRED, reason="; ".join(pauses)[:500])
    session.commit()
    return app


# ---- User edits and readiness ------------------------------------------------------------------

def _reopen_for_edit(session: Session, app: Application) -> None:
    status = Status(app.status)
    if status not in EDITABLE:
        raise AssistantError(f"Answers can't be changed while the application is {status}")
    if status == Status.AWAITING_APPROVAL:
        transition(session, app, Status.CHANGES_REQUESTED, reason="User edited an answer")
        transition(session, app, Status.APPLICATION_PREPARED, reason="Back to preparation after edit")


def reuse_scope(row: ApplicationAnswer, value: str | None = None) -> tuple[str | None, str]:
    """Where an answer would be saved for future applications: (key, description), or (None, why not)."""
    cat = row.category
    if cat in NEVER_SAVE:
        if cat in ("work_auth", "sponsorship", "citizenship"):
            return None, "Work authorization comes from the verified facts in your profile"
        if cat == "legal":
            return None, "Legal statements are answered fresh on every application"
        return None, "Files come from the approved resume for each job"
    if cat in PROFILE_FIELDS:
        return f"profile:{PROFILE_FIELDS[cat]}", f"Save as your {cat} in your profile"
    if cat == "prior_employment":
        if value is None or value.strip().lower().startswith("no"):
            return "prior_employment", "Answer 'No' for any company that isn't in your experience"
        return slug(row.question_text), "Use for this exact question only"
    if cat in REUSE_BY_CATEGORY:
        return cat, f"Use for every {cat.replace('_', ' ')} question"
    return slug(row.question_text), "Use whenever this exact question appears"


def save_for_future(session: Session, row: ApplicationAnswer, value: str) -> str:
    key, description = reuse_scope(row, value)
    if key is None:
        raise AssistantError(f"Can't save this answer: {description}")
    stored = session.get(UserProfile, 1)
    profile = Profile.model_validate(stored.data if stored else {})
    if key.startswith("profile:"):
        path = key.split(":", 1)[1]
        if path.startswith("links."):
            profile.links[path.split(".", 1)[1]] = value
        else:
            setattr(profile, path, value)
    else:
        profile.approved_answers[key] = value
    stored = stored or UserProfile(id=1)
    stored.data = profile.model_dump(mode="json")
    session.add(stored)
    return description


def update_answer(session: Session, app: Application, question_key: str, answer: str | None, *,
                  confirm: bool, save: bool = False) -> ApplicationAnswer:
    row = next((a for a in app.answers if a.question_key == question_key), None)
    if row is None:
        raise AssistantError("Unknown question")
    value = (answer or "").strip() or None
    if confirm:
        if value is None and row.required:
            raise AssistantError("This question is required")
        if value and PLACEHOLDER.search(value):
            raise AssistantError("Replace the [ADD: …] placeholders before confirming")
        if value and row.options and value not in row.options:
            raise AssistantError(f"Choose one of: {', '.join(row.options)}")
    if save and reuse_scope(row, value)[0] is None:
        raise AssistantError(f"Can't save this answer: {reuse_scope(row, value)[1]}")
    _reopen_for_edit(session, app)
    changed = value != row.answer
    row.answer = value
    if confirm:
        row.verification_status = "USER_CONFIRMED" if value else "SKIPPED"
        row.source = "user" if changed or row.source is None else row.source
        row.note = "Confirmed by you"
    if save:
        if not (confirm and value):
            raise AssistantError("Only a confirmed, non-empty answer can be saved for future applications")
        row.note = f"Confirmed by you; saved for future applications ({save_for_future(session, row, value)})"
    _refresh_hash(session, app)
    if Status(app.status) == Status.INVALIDATED:
        transition(session, app, Status.APPLICATION_PREPARED, reason="Answer changed after approval")
    session.commit()
    return row


def mark_ready(session: Session, app: Application) -> Application:
    """Move to AWAITING_APPROVAL once nothing is outstanding."""
    status = Status(app.status)
    left = outstanding(app)
    if left["needs_answer"] or left["drafts_to_confirm"]:
        raise AssistantError(f"{len(left['needs_answer'])} question(s) need answers and "
                             f"{len(left['drafts_to_confirm'])} draft(s) need confirming")
    if app.resume_version is None or app.resume_version.review_status != "APPROVED":
        raise AssistantError("The resume must be approved")
    if status == Status.REVIEW_REQUIRED:
        raise AssistantError("Resolve the open review first (re-run the dry run once the issue is fixed)")
    if status != Status.APPLICATION_PREPARED:
        raise AssistantError(f"Application is {status}, not APPLICATION_PREPARED")
    if app.form_snapshot_hash != hash_snapshot(build_snapshot(app)):
        raise AssistantError("Snapshot hash is stale; reload and try again")
    transition(session, app, Status.AWAITING_APPROVAL, reason="All answers confirmed; ready for your approval")
    session.commit()
    return app


def submit(session: Session, app: Application) -> None:
    """Real submission is deliberately not implemented in this build."""
    raise AssistantError("Dry-run mode: ApplyPilot does not submit applications. Submit it yourself on the "
                         "employer's site if you want to apply.")
