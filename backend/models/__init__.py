"""Database tables (design doc section 5)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base
from backend.models.states import Status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "source_job_id", name="uq_job_source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    source_job_id: Mapped[str | None] = mapped_column(String(128))
    company: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(512))
    location: Mapped[str | None] = mapped_column(String(512))
    employment_type: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text, unique=True)
    requisition_id: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # VERIFIED (source gave a publication date), UNVERIFIED (no reliable date evidence)
    freshness_confidence: Mapped[str] = mapped_column(String(16), default="UNVERIFIED")
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # company|title|location
    possible_duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))

    scores: Mapped[list[JobScore]] = relationship(back_populates="job", cascade="all, delete-orphan")
    application: Mapped[Application | None] = relationship(back_populates="job", uselist=False)


class JobScore(Base):
    __tablename__ = "job_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    skills_score: Mapped[float] = mapped_column(Float)
    eligibility_status: Mapped[str] = mapped_column(String(8))  # PASS / REVIEW / FAIL
    match_reasons: Mapped[list] = mapped_column(JSON, default=list)
    gaps: Mapped[list] = mapped_column(JSON, default=list)
    eligibility_notes: Mapped[list] = mapped_column(JSON, default=list)
    analysis: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # structured LLM job analysis, if used
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="scores")


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    file_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    review_status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/APPROVED/REJECTED
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[dict | None] = mapped_column(JSON)  # verified draft with source fact ids per bullet
    report: Mapped[dict | None] = mapped_column(JSON)  # matches, gaps, review claims, quality checks


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)  # one per job: no resubmits
    status: Mapped[str] = mapped_column(String(32), default=Status.DISCOVERED)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id"))
    form_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_id: Mapped[str | None] = mapped_column(String(256))
    last_error: Mapped[str | None] = mapped_column(Text)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_url: Mapped[str | None] = mapped_column(Text)
    prep: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # latest dry-run report
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # Optimistic locking guards against two workers moving the same application at once.
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": version}

    job: Mapped[Job] = relationship(back_populates="application")
    resume_version: Mapped[ResumeVersion | None] = relationship()
    events: Mapped[list[ApplicationEvent]] = relationship(order_by="ApplicationEvent.id")
    answers: Mapped[list[ApplicationAnswer]] = relationship(order_by="ApplicationAnswer.position",
                                                            cascade="all, delete-orphan")


class ApplicationAnswer(Base):
    """One form field of a prepared application and the answer ApplyPilot intends to give."""
    __tablename__ = "application_answers"
    __table_args__ = (UniqueConstraint("application_id", "question_key", name="uq_answer_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    question_key: Mapped[str] = mapped_column(String(256))
    question_text: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    # VERIFIED (from profile / approved answers), DRAFT (model-written, needs confirmation),
    # NEEDS_USER, USER_CONFIRMED, SKIPPED (optional, left blank)
    verification_status: Mapped[str] = mapped_column(String(16), default="NEEDS_USER")
    category: Mapped[str] = mapped_column(String(32), default="unknown")
    source: Mapped[str | None] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(16), default="text")
    required: Mapped[bool] = mapped_column(default=False)
    sensitive: Mapped[bool] = mapped_column(default=False)
    options: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    resume_hash: Mapped[str] = mapped_column(String(64))
    form_hash: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE/CONSUMED/INVALIDATED


class ApplicationEvent(Base):
    """Append-only audit trail of every status transition."""
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    old_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    details: Mapped[str | None] = mapped_column(Text)
    evidence_path: Mapped[str | None] = mapped_column(Text)


class UserProfile(Base):
    """Single-row store of verified career facts and search preferences."""
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


__all__ = ["Job", "JobScore", "ResumeVersion", "Application", "ApplicationAnswer",
           "ApprovalEvent", "ApplicationEvent", "UserProfile", "Status", "utcnow"]
