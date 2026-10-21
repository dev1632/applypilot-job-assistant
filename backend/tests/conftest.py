from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ["APPLYPILOT_USE_LLM"] = "false"
os.environ["APPLYPILOT_HTTP_DELAY_SECONDS"] = "0"

from backend.config import get_settings  # noqa: E402
from backend.db import SessionLocal, init_db  # noqa: E402
from backend.models import utcnow  # noqa: E402
from backend.models.profile import Education, Profile, WorkAuthorization  # noqa: E402
from backend.sources import RawPosting, SourceAdapter  # noqa: E402


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    init_db(f"sqlite:///{tmp_path / 'test.db'}")
    with SessionLocal() as s:
        yield s


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="Test User", email="test@example.com",
        skills=["Python", "React", "TypeScript", "AWS", "PostgreSQL", "Docker"],
        education=[Education(school="Example U", degree="MS", field="CS", expected_graduation="2026-12")],
        work_authorization=WorkAuthorization(authorized_in_us=True, requires_sponsorship_now=False,
                                             requires_sponsorship_future=True, us_citizen=False),
    )


def posting(**kw) -> RawPosting:
    base = dict(source="fake", source_job_id="1", company="Acme", title="Software Engineer",
                location="New York, NY", employment_type=None, url="https://jobs.acme.com/1?utm_source=x",
                requisition_id=None, description="We use Python, React and AWS.",
                posted_at=utcnow() - timedelta(hours=3), freshness_confidence="VERIFIED")
    base.update(kw)
    return RawPosting(**base)


class FakeAdapter(SourceAdapter):
    name = "fake"

    def __init__(self, postings):
        self.board = "fake"
        self.postings = postings

    def fetch(self):
        return list(self.postings)
