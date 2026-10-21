"""ApplyPilot API. Run with: uvicorn backend.api.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents import application_tracker as tracker
from backend.agents import application_assistant as assistant
from backend.agents import resume_tailor
from backend.agents.job_discovery import evaluate, run_scan, triage, upsert_posting, ScanReport
from backend.api.schemas import (ApplicationOut, ApproveIn, EventOut, FollowUpIn, JobCreate, JobDetail,
                                 JobOut, PastedJobIn, PrepareIn, ResumeOut, ResumeReviewIn, ScoreOut, TransitionIn, AnswerIn)
from backend.config import get_settings
from backend.db import get_session, init_db
from backend.models import Application, ApprovalEvent, Job, ResumeVersion, UserProfile
from backend.models.profile import Profile
from backend.models.states import GATED, Status, allowed_next
from backend.safety import approval
from backend.services import resume_import
from backend.services.llm import get_llm
from backend.sources import RawPosting


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="ApplyPilot", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

DB = Depends(get_session)


def load_profile(session: Session) -> Profile:
    row = session.get(UserProfile, 1)
    return Profile.model_validate(row.data) if row else Profile()


def _job_out(job: Job, cls=JobOut, **extra):
    latest = max(job.scores, key=lambda s: s.id) if job.scores else None
    return cls.model_validate(job).model_copy(update={
        "status": job.application.status if job.application else None,
        "latest_score": ScoreOut.model_validate(latest) if latest else None, **extra})


def _get_app(session: Session, app_id: int) -> Application:
    app_ = session.get(Application, app_id)
    if app_ is None:
        raise HTTPException(404, "Application not found")
    return app_


@app.get("/health")
def health():
    s = get_settings()
    return {"ok": True, "llm_enabled": s.applypilot_use_llm and bool(s.resolve_openai_key()),
            "model": s.openai_model, "data_dir": str(s.data_dir)}


@app.get("/stats")
def stats(session: Session = DB):
    rows = session.execute(select(Application.status, func.count()).group_by(Application.status)).all()
    return {status: count for status, count in rows}


@app.get("/profile", response_model=Profile)
def get_profile(session: Session = DB):
    return load_profile(session)


@app.put("/profile", response_model=Profile)
def put_profile(profile: Profile, session: Session = DB):
    row = session.get(UserProfile, 1) or UserProfile(id=1)
    row.data = profile.model_dump(mode="json")
    session.add(row)
    session.commit()
    return profile


@app.post("/profile/imports", status_code=201)
async def import_resume(file: UploadFile):
    """Upload an existing resume; returns a draft for review. The profile isn't changed."""
    data = await file.read(resume_import.MAX_BYTES + 1)
    try:
        return resume_import.create_import(data, file.filename or "", get_llm())
    except resume_import.ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/profile/imports/{import_id}")
def get_import(import_id: str):
    try:
        return resume_import.load_import(import_id)
    except resume_import.ImportError_ as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/profile/imports/{import_id}/apply")
def apply_import(import_id: str, body: resume_import.ImportSelection, session: Session = DB):
    """Merge the items the user picked into the verified profile."""
    try:
        record = resume_import.load_import(import_id)
        merged, log = resume_import.apply_import(load_profile(session), record, body)
    except resume_import.ImportError_ as exc:
        raise HTTPException(409, str(exc)) from exc
    row = session.get(UserProfile, 1) or UserProfile(id=1)
    row.data = merged.model_dump(mode="json")
    session.add(row)
    session.commit()
    return {"profile": merged, "changes": log}


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(status: str | None = None, freshness: str | None = None, limit: int = 200, session: Session = DB):
    q = select(Job).outerjoin(Application).order_by(Job.discovered_at.desc()).limit(min(limit, 1000))
    if status:
        q = q.where(Application.status == status)
    if freshness:
        q = q.where(Job.freshness_confidence == freshness)
    return [_job_out(j) for j in session.scalars(q)]


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: Session = DB):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    app_ = job.application
    allowed: list[str] = []
    if app_:
        prev = Status(app_.previous_status) if app_.previous_status else None
        allowed = sorted(s for s in allowed_next(Status(app_.status), prev) if s not in GATED)
    return _job_out(job, JobDetail, description=job.description,
                    application=ApplicationOut.model_validate(app_) if app_ else None,
                    events=[EventOut.model_validate(e) for e in app_.events] if app_ else [],
                    allowed_next=allowed)


@app.post("/jobs", response_model=JobDetail, status_code=201)
def create_job(body: JobCreate, session: Session = DB):
    """Manually add a posting (e.g. from a source without an adapter)."""
    raw = RawPosting(source="manual", source_job_id=None, company=body.company, title=body.title,
                     location=body.location, employment_type=body.employment_type, url=body.url,
                     requisition_id=body.requisition_id, description=body.description, posted_at=body.posted_at,
                     freshness_confidence="VERIFIED" if body.posted_at else "UNVERIFIED")
    job, created = upsert_posting(session, raw)
    if not created:
        raise HTTPException(409, f"Job already tracked as id {job.id}")
    profile = load_profile(session)
    triage(session, job, evaluate(session, job, profile, get_llm()), profile, ScanReport())
    session.commit()
    return get_job(job.id, session)


@app.post("/jobs/{job_id}/rescore", response_model=ScoreOut)
def rescore(job_id: int, session: Session = DB):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    score = evaluate(session, job, load_profile(session), get_llm())
    session.commit()
    return score


@app.post("/discovery/scan")
def scan(session: Session = DB):
    profile = load_profile(session)
    if not profile.preferences.sources:
        raise HTTPException(400, "No sources configured in profile.preferences.sources")
    return asdict(run_scan(session, profile, llm=get_llm()))


@app.get("/applications", response_model=list[ApplicationOut])
def list_applications(status: str | None = None, session: Session = DB):
    q = select(Application).order_by(Application.updated_at.desc())
    if status:
        q = q.where(Application.status == status)
    return list(session.scalars(q))


@app.get("/applications/{app_id}/events", response_model=list[EventOut])
def application_events(app_id: int, session: Session = DB):
    return _get_app(session, app_id).events


@app.post("/applications/{app_id}/transition", response_model=ApplicationOut)
def transition(app_id: int, body: TransitionIn, session: Session = DB):
    app_ = _get_app(session, app_id)
    try:
        tracker.transition(session, app_, body.status, reason=body.reason)
    except (tracker.TransitionError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return app_


@app.get("/applications/{app_id}/review")
def review(app_id: int, session: Session = DB):
    """What the user must see before approving; the hashes must be echoed back to /approve."""
    app_ = _get_app(session, app_id)
    return {"application_id": app_.id, "job_id": app_.job_id, "status": app_.status,
            "resume_version_id": app_.resume_version_id,
            "resume_hash": app_.resume_version.content_hash if app_.resume_version else None,
            "form_hash": app_.form_snapshot_hash}


@app.post("/applications/{app_id}/approve", response_model=ApplicationOut)
def approve(app_id: int, body: ApproveIn, session: Session = DB):
    app_ = _get_app(session, app_id)
    try:
        approval.approve(session, app_, job_id=body.job_id, resume_hash=body.resume_hash, form_hash=body.form_hash)
    except approval.ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return app_


@app.post("/applications/{app_id}/follow-up", response_model=ApplicationOut)
def follow_up(app_id: int, body: FollowUpIn, session: Session = DB):
    app_ = _get_app(session, app_id)
    tracker.set_follow_up(session, app_, body.when)
    session.commit()
    return app_


@app.get("/reminders", response_model=list[ApplicationOut])
def reminders(session: Session = DB):
    return tracker.due_follow_ups(session)


@app.post("/jobs/{job_id}/resume", response_model=ResumeOut, status_code=201)
def tailor(job_id: int, session: Session = DB):
    """Agent 2: generate a new verified, job-specific resume version."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    try:
        outcome = resume_tailor.tailor_resume(session, job, load_profile(session), get_llm(),
                                              standalone=job.source == "pasted")
    except resume_tailor.TailoringError as exc:
        raise HTTPException(409, str(exc)) from exc
    return outcome.version


@app.post("/resumes/from-description", status_code=201)
def resume_from_description(body: PastedJobIn, session: Session = DB):
    """Paste a job description, get a tailored resume scored for ATS. Pasting the same posting again
    adds a new version to the same job."""
    import hashlib

    title, company, desc = body.title.strip(), body.company.strip(), body.description.strip()
    if not title or not company:
        raise HTTPException(422, "Job title and company are required")
    if not 200 <= len(desc) <= 30000:
        raise HTTPException(422, "Paste the full job description (200 to 30,000 characters)")
    digest = hashlib.sha256(f"{company.lower()}|{title.lower()}|{' '.join(desc.split())}".encode()).hexdigest()[:20]
    url = body.url.strip() if body.url and body.url.strip().startswith(("http://", "https://")) else \
        f"https://pasted.applypilot.local/{digest}"
    raw = RawPosting(source="pasted", source_job_id=digest, company=company, title=title, location=body.location,
                     employment_type=None, url=url, requisition_id=None, description=desc, posted_at=None,
                     freshness_confidence="UNVERIFIED")
    profile = load_profile(session)
    if not profile.experience and not profile.projects:
        raise HTTPException(409, "Your profile has no experience yet. Import your resume first.")
    job, created = upsert_posting(session, raw)
    llm = get_llm()
    if created:
        triage(session, job, evaluate(session, job, profile, llm), profile, ScanReport())
        session.commit()
    try:
        outcome = resume_tailor.tailor_resume(session, job, profile, llm, standalone=True)
    except resume_tailor.TailoringError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"job_id": job.id, "resume": ResumeOut.model_validate(outcome.version).model_dump(mode="json")}


@app.get("/jobs/{job_id}/resumes", response_model=list[ResumeOut])
def job_resumes(job_id: int, session: Session = DB):
    return list(session.scalars(select(ResumeVersion).where(ResumeVersion.job_id == job_id)
                                .order_by(ResumeVersion.version.desc())))


def _get_resume(session: Session, resume_id: int) -> ResumeVersion:
    version = session.get(ResumeVersion, resume_id)
    if version is None:
        raise HTTPException(404, "Resume version not found")
    return version


@app.get("/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: int, session: Session = DB):
    return _get_resume(session, resume_id)


@app.get("/resumes/{resume_id}/download")
def download_resume(resume_id: int, session: Session = DB):
    version = _get_resume(session, resume_id)
    path = Path(version.file_path).resolve()
    if not path.is_relative_to(get_settings().data_dir / "resumes") or not path.exists():
        raise HTTPException(404, "Resume file not found")
    return FileResponse(path, filename=f"resume_job{version.job_id}_v{version.version}.docx")


@app.post("/resumes/{resume_id}/review", response_model=ResumeOut)
def review_resume(resume_id: int, body: ResumeReviewIn, session: Session = DB):
    try:
        return resume_tailor.review_resume(session, _get_resume(session, resume_id),
                                           approve=body.approve, note=body.note)
    except resume_tailor.TailoringError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---- Agent 3: Application Assistant (dry run) ---------------------------------------------------

def _app_detail(session: Session, app_: Application) -> dict:
    job = session.get(Job, app_.job_id)
    approvals = session.scalars(select(ApprovalEvent).where(ApprovalEvent.application_id == app_.id)
                                .order_by(ApprovalEvent.id.desc()))
    rv = app_.resume_version
    return {
        "application": ApplicationOut.model_validate(app_).model_dump(mode="json"),
        "job": {"id": job.id, "company": job.company, "title": job.title, "location": job.location, "url": job.url,
                "requisition_id": job.requisition_id},
        "resume": None if rv is None else {"id": rv.id, "version": rv.version, "content_hash": rv.content_hash,
                                           "review_status": rv.review_status},
        "application_url": app_.application_url,
        "prep": app_.prep,
        "answers": [{"question_key": a.question_key, "question_text": a.question_text, "answer": a.answer,
                     "verification_status": a.verification_status, "category": a.category, "source": a.source,
                     "field_type": a.field_type, "required": a.required, "sensitive": a.sensitive,
                     "options": a.options, "note": a.note,
                     "reuse": dict(zip(("key", "description"), assistant.reuse_scope(a)))} for a in app_.answers],
        "outstanding": assistant.outstanding(app_),
        "approvals": [{"id": e.id, "approved_at": e.approved_at, "consumed_at": e.consumed_at,
                       "approval_status": e.approval_status, "resume_hash": e.resume_hash, "form_hash": e.form_hash}
                      for e in approvals],
        "submission_mode": assistant.SUBMISSION_MODE,
    }


@app.post("/jobs/{job_id}/prepare")
def prepare(job_id: int, body: PrepareIn, session: Session = DB):
    """Fill the application form in a sandboxed browser. Never submits."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    try:
        app_ = assistant.prepare_application(session, job, load_profile(session), get_llm(),
                                             application_url=body.application_url, headless=body.headless)
    except assistant.AssistantError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _app_detail(session, app_)


@app.get("/applications/{app_id}")
def application_detail(app_id: int, session: Session = DB):
    return _app_detail(session, _get_app(session, app_id))


@app.post("/applications/{app_id}/answers")
def answer(app_id: int, body: AnswerIn, session: Session = DB):
    app_ = _get_app(session, app_id)
    try:
        assistant.update_answer(session, app_, body.question_key, body.answer, confirm=body.confirm, save=body.save)
    except assistant.AssistantError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _app_detail(session, app_)


@app.post("/applications/{app_id}/ready")
def ready(app_id: int, session: Session = DB):
    app_ = _get_app(session, app_id)
    try:
        assistant.mark_ready(session, app_)
    except assistant.AssistantError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _app_detail(session, app_)


@app.get("/applications/{app_id}/screenshot")
def screenshot(app_id: int, session: Session = DB):
    app_ = _get_app(session, app_id)
    shot = (app_.prep or {}).get("screenshot")
    path = Path(shot).resolve() if shot else None
    if path is None or not path.is_relative_to(get_settings().data_dir / "applications") or not path.exists():
        raise HTTPException(404, "No screenshot")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/applications/{app_id}/submit")
def submit(app_id: int, session: Session = DB):
    try:
        assistant.submit(session, _get_app(session, app_id))
    except assistant.AssistantError as exc:
        raise HTTPException(403, str(exc)) from exc


if get_settings().applypilot_dev_routes:
    _FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

    @app.get("/dev/forms/{name}", include_in_schema=False)
    def dev_form(name: str):
        """Local practice forms for trying the dry run (APPLYPILOT_DEV_ROUTES=true)."""
        path = (_FIXTURES / name).resolve()
        if not path.is_relative_to(_FIXTURES) or path.suffix != ".html" or not path.exists():
            raise HTTPException(404, "No such form")
        return FileResponse(path, media_type="text/html")
