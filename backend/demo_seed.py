"""Fill a separate data directory with sample data to try the dashboard:
    APPLYPILOT_DATA_DIR=./demo_data APPLYPILOT_USE_LLM=false python -m backend.demo_seed
"""
from __future__ import annotations

from datetime import timedelta

from backend.agents.job_discovery import ScanReport, evaluate, triage, upsert_posting
from backend.agents.resume_tailor import review_resume, tailor_resume
from backend.config import get_settings
from backend.db import SessionLocal, init_db
from backend.models import Job, UserProfile, utcnow
from backend.models.profile import Profile
from backend.sources import RawPosting

PROFILE = {
    "name": "Demo Candidate", "email": "demo@example.com", "links": {"github": "https://github.com/demo"},
    "skills": ["Python", "TypeScript", "React", "FastAPI", "PostgreSQL", "AWS", "Docker", "PyTorch", "SQL"],
    "education": [{"school": "Example University", "degree": "MS", "field": "Computer Science",
                   "expected_graduation": "2026-12", "coursework": ["Distributed Systems", "Machine Learning", "Databases"]}],
    "experience": [
        {"id": "acme", "title": "Software Engineering Intern", "organization": "Acme Corp", "start": "2025-05",
         "end": "2025-08", "technologies": ["Python", "FastAPI", "React", "PostgreSQL"],
         "bullets": ["Built REST APIs in Python serving 2,000 daily users",
                     "Wrote React components for the internal admin dashboard",
                     "Contributed to migrating CI pipelines to GitHub Actions"]},
        {"id": "lab", "title": "Graduate Research Assistant", "organization": "Example University", "start": "2025-09",
         "end": None, "technologies": ["Python", "PyTorch"],
         "bullets": ["Trained PyTorch text classifiers on a 50,000-document corpus"]},
    ],
    "projects": [{"id": "bot", "name": "Course Q&A Chatbot", "description": "Retrieval-augmented chatbot over course notes",
                  "technologies": ["Python", "AWS"], "outcomes": ["Used by 40 students during a pilot"]}],
    "work_authorization": {"authorized_in_us": True, "requires_sponsorship_now": False,
                           "requires_sponsorship_future": True, "us_citizen": False},
    "preferences": {"locations": ["New York", "Remote"], "employment_types": ["full-time"]},
}

PRACTICE_FORM = "http://127.0.0.1:8000/dev/forms/application_form.html"

JOBS = [
    ("Northwind", "Software Engineer, New Grad", "New York, NY", 5,
     "Build services in Python and React on AWS. PostgreSQL experience a plus. Kubernetes preferred."),
    ("Contoso AI", "Machine Learning Engineer I", "Remote - US", 10,
     "Train and deploy PyTorch models. Python, SQL, Docker required. We will sponsor visas."),
    ("Fabrikam", "Full Stack Engineer", "New York, NY", 20,
     "TypeScript, React, Node.js, GraphQL. We are unable to sponsor visas for this position."),
    ("Tailspin", "Data Engineer", "Austin, TX", 8,
     "Spark, Airflow, Snowflake, Python and SQL pipelines."),
    ("Litware", "Software Engineer", "New York, NY", 72,
     "Java, Spring, Kafka, microservices."),
]


def main() -> None:
    init_db()
    with SessionLocal() as session:
        if session.query(Job).count():
            raise SystemExit(f"{get_settings().data_dir} already has data; use an empty demo directory")
        session.add(UserProfile(id=1, data=Profile.model_validate(PROFILE).model_dump(mode="json")))
        profile = Profile.model_validate(PROFILE)
        for i, (company, title, location, hours, desc) in enumerate(JOBS):
            # The first job points at the local practice form (needs APPLYPILOT_DEV_ROUTES=true).
            url = PRACTICE_FORM if i == 0 else f"https://example.com/{company.lower()}/{i}"
            raw = RawPosting(source="demo", source_job_id=str(i), company=company, title=title, location=location,
                             employment_type=None, url=url, requisition_id="R-1042" if i == 0 else None, description=desc, posted_at=utcnow() - timedelta(hours=hours),
                             freshness_confidence="VERIFIED")
            job, _ = upsert_posting(session, raw)
            triage(session, job, evaluate(session, job, profile, None), profile, ScanReport())
        session.commit()
        first = session.query(Job).order_by(Job.id).first()
        version = tailor_resume(session, first, profile, llm=None).version
        review_resume(session, version, approve=True, note="Demo: pre-approved so you can try the dry run")
    print(f"Demo data written to {get_settings().data_dir}")


if __name__ == "__main__":
    main()
