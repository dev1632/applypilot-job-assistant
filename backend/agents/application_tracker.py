"""Agent 4: the single authority for application state. All status changes go through here."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Application, ApplicationEvent, Job, utcnow
from backend.models.states import GATED, Status, allowed_next


class TransitionError(ValueError):
    pass


def ensure_application(session: Session, job: Job) -> Application:
    app = session.scalar(select(Application).where(Application.job_id == job.id))
    if app is None:
        app = Application(job_id=job.id, status=Status.DISCOVERED)
        session.add(app)
        session.flush()
        session.add(ApplicationEvent(application_id=app.id, old_status=None,
                                     new_status=Status.DISCOVERED, details=f"Discovered via {job.source}"))
    return app


def transition(session: Session, app: Application, new: Status | str, *, reason: str,
               evidence_path: str | None = None, _gate: bool = False) -> Application:
    """Move an application to a new status, recording an append-only event.

    APPROVED / SUBMITTING / SUBMITTED may only be entered by the approval gate (_gate=True).
    """
    new = Status(new)
    current = Status(app.status)
    previous = Status(app.previous_status) if app.previous_status else None
    if new not in allowed_next(current, previous):
        raise TransitionError(f"{current} -> {new} is not allowed")
    if new in GATED and not _gate:
        raise TransitionError(f"{new} can only be set by the approval/submission gate")
    if new == Status.SUBMITTED and not (evidence_path or app.confirmation_id):
        raise TransitionError("SUBMITTED requires confirmation evidence")

    app.previous_status = current
    app.status = new
    session.add(ApplicationEvent(application_id=app.id, old_status=current, new_status=new,
                                 details=reason, evidence_path=evidence_path))
    session.flush()
    return app


def set_follow_up(session: Session, app: Application, when: datetime) -> None:
    app.follow_up_at = when
    session.add(ApplicationEvent(application_id=app.id, old_status=app.status, new_status=app.status,
                                 details=f"Follow-up reminder set for {when.isoformat()}"))


def due_follow_ups(session: Session, now: datetime | None = None) -> list[Application]:
    now = now or utcnow()
    return list(session.scalars(select(Application).where(Application.follow_up_at <= now)))
