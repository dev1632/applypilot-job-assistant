"""Backend-enforced approval gate (design doc sections 3 & 6).

An approval is bound to the exact job, resume content hash, and form snapshot hash. Any change to
those after approval invalidates it. An approval authorizes exactly one submission attempt.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.application_tracker import TransitionError, transition
from backend.models import Application, ApprovalEvent, utcnow
from backend.models.states import SUBMITTED_LIKE, Status


class ApprovalError(PermissionError):
    pass


def _current_hashes(app: Application) -> tuple[str, str]:
    if app.resume_version is None or not app.form_snapshot_hash:
        raise ApprovalError("Application has no resume version or form snapshot to approve")
    return app.resume_version.content_hash, app.form_snapshot_hash


def _active_approval(session: Session, app: Application) -> ApprovalEvent | None:
    return session.scalar(select(ApprovalEvent).where(
        ApprovalEvent.application_id == app.id, ApprovalEvent.approval_status == "ACTIVE"))


def approve(session: Session, app: Application, *, job_id: int, resume_hash: str, form_hash: str) -> ApprovalEvent:
    """Record explicit user approval. The caller must echo back the hashes the user reviewed."""
    if Status(app.status) != Status.AWAITING_APPROVAL:
        raise ApprovalError(f"Application is {app.status}, not AWAITING_APPROVAL")
    cur_resume, cur_form = _current_hashes(app)
    if (job_id, resume_hash, form_hash) != (app.job_id, cur_resume, cur_form):
        raise ApprovalError("Reviewed content does not match the current application; review again")
    event = ApprovalEvent(application_id=app.id, job_id=app.job_id, resume_hash=resume_hash, form_hash=form_hash)
    session.add(event)
    transition(session, app, Status.APPROVED, reason="Explicit user approval", _gate=True)
    return event


def invalidate(session: Session, app: Application, reason: str) -> None:
    approval = _active_approval(session, app)
    if approval:
        approval.approval_status = "INVALIDATED"
    if Status(app.status) == Status.APPROVED:
        transition(session, app, Status.INVALIDATED, reason=reason)


def on_content_changed(session: Session, app: Application, *, resume_version_id: int | None = None,
                       form_snapshot_hash: str | None = None) -> None:
    """Call whenever the resume or form changes; stale approvals are revoked."""
    if Status(app.status) in SUBMITTED_LIKE:
        raise ApprovalError("Cannot change content of an application that is submitted or submitting")
    changed = False
    if resume_version_id is not None and resume_version_id != app.resume_version_id:
        app.resume_version_id = resume_version_id
        changed = True
    if form_snapshot_hash is not None and form_snapshot_hash != app.form_snapshot_hash:
        app.form_snapshot_hash = form_snapshot_hash
        changed = True
    session.flush()
    session.refresh(app)
    if changed:
        invalidate(session, app, "Resume or form changed after approval")


def begin_submission(session: Session, app: Application) -> ApprovalEvent:
    """Consume the approval and move to SUBMITTING. Call immediately before the single submit click."""
    if Status(app.status) != Status.APPROVED:
        raise ApprovalError(f"Submission refused: application is {app.status}, not APPROVED")
    approval = _active_approval(session, app)
    if approval is None or approval.consumed_at is not None:
        raise ApprovalError("Submission refused: no unused approval on record")
    cur_resume, cur_form = _current_hashes(app)
    if (approval.job_id, approval.resume_hash, approval.form_hash) != (app.job_id, cur_resume, cur_form):
        invalidate(session, app, "Content drifted from the approved snapshot")
        raise ApprovalError("Submission refused: content changed since approval")
    approval.consumed_at = utcnow()
    approval.approval_status = "CONSUMED"
    transition(session, app, Status.SUBMITTING, reason="Approval consumed; single submission attempt", _gate=True)
    return approval


def record_submission_result(session: Session, app: Application, *, confirmed: bool | None,
                             confirmation_id: str | None = None, evidence_path: str | None = None,
                             error: str | None = None) -> None:
    """confirmed=True needs evidence; None means ambiguous (never auto-retried)."""
    if Status(app.status) != Status.SUBMITTING:
        raise TransitionError("No submission in progress")
    if confirmed is True:
        if not (confirmation_id or evidence_path):
            raise TransitionError("A verified submission requires a confirmation id or evidence")
        app.confirmation_id = confirmation_id
        app.submitted_at = utcnow()
        transition(session, app, Status.SUBMITTED, reason="Submission verified",
                   evidence_path=evidence_path, _gate=True)
    elif confirmed is None:
        app.last_error = error
        transition(session, app, Status.SUBMISSION_UNKNOWN, reason=error or "Ambiguous result",
                   evidence_path=evidence_path)
    else:
        app.last_error = error
        transition(session, app, Status.FAILED, reason=error or "Submission failed", evidence_path=evidence_path)


def resolve_manual_verification(session: Session, app: Application, *, submitted: bool,
                                confirmation_id: str | None = None, evidence_path: str | None = None,
                                note: str = "") -> None:
    """User confirms by hand whether an ambiguous submission actually went through."""
    if Status(app.status) != Status.MANUAL_VERIFICATION:
        raise TransitionError("Application is not awaiting manual verification")
    if submitted:
        if not (confirmation_id or evidence_path):
            raise TransitionError("Marking as submitted requires a confirmation id or evidence")
        app.confirmation_id = confirmation_id
        app.submitted_at = app.submitted_at or utcnow()
        transition(session, app, Status.SUBMITTED, reason=f"Manually verified. {note}".strip(),
                   evidence_path=evidence_path, _gate=True)
    else:
        transition(session, app, Status.FAILED, reason=f"Manually verified as not submitted. {note}".strip())
