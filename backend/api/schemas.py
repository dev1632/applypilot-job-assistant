from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ScoreOut(ORM):
    skills_score: float
    eligibility_status: str
    match_reasons: list[str]
    gaps: list[str]
    eligibility_notes: list[str]
    scored_at: datetime


class EventOut(ORM):
    id: int
    old_status: str | None
    new_status: str
    timestamp: datetime
    details: str | None
    evidence_path: str | None


class ApplicationOut(ORM):
    id: int
    job_id: int
    status: str
    previous_status: str | None
    resume_version_id: int | None
    form_snapshot_hash: str | None
    submitted_at: datetime | None
    confirmation_id: str | None
    last_error: str | None
    follow_up_at: datetime | None


class JobOut(ORM):
    id: int
    source: str
    company: str
    title: str
    location: str | None
    employment_type: str | None
    url: str
    requisition_id: str | None
    posted_at: datetime | None
    discovered_at: datetime
    freshness_confidence: str
    possible_duplicate_of: int | None
    status: str | None = None
    latest_score: ScoreOut | None = None


class JobDetail(JobOut):
    description: str | None
    application: ApplicationOut | None = None
    events: list[EventOut] = []
    allowed_next: list[str] = []  # statuses the user may move to by hand (gated ones excluded)


class JobCreate(BaseModel):
    company: str
    title: str
    url: str
    location: str | None = None
    employment_type: str | None = None
    description: str = ""
    posted_at: datetime | None = None
    requisition_id: str | None = None


class TransitionIn(BaseModel):
    status: str
    reason: str


class ApproveIn(BaseModel):
    job_id: int
    resume_hash: str
    form_hash: str


class FollowUpIn(BaseModel):
    when: datetime


class ResumeOut(ORM):
    id: int
    job_id: int
    version: int
    content_hash: str
    created_at: datetime
    review_status: str
    approved_at: datetime | None
    content: dict | None
    report: dict | None


class ResumeReviewIn(BaseModel):
    approve: bool
    note: str = ""


class PrepareIn(BaseModel):
    application_url: str | None = None  # defaults to the job's posting URL
    headless: bool = True


class AnswerIn(BaseModel):
    question_key: str
    answer: str | None
    confirm: bool = True
    save: bool = False  # also save it for future applications


class PastedJobIn(BaseModel):
    title: str
    company: str
    description: str
    url: str | None = None
    location: str | None = None
