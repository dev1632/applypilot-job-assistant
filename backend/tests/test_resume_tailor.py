import pytest

from backend.agents.application_tracker import ensure_application, transition
from backend.agents.job_discovery import upsert_posting
from backend.agents.resume_tailor import TailoringError, review_resume, tailor_resume
from backend.models import ResumeVersion
from backend.models.profile import Experience, Project
from backend.models.resume import DraftBullet, DraftEntry, TailoredDraft
from backend.models.states import Status
from backend.services.facts import build_fact_store
from backend.safety.resume_verifier import verify_draft
from backend.services.hashing import hash_file
from backend.services.llm import JobAnalysis
from backend.tests.conftest import posting

DESCRIPTION = "Build APIs in Python and React on AWS. Experience with Kubernetes preferred."


class FakeLLM:
    """Returns queued drafts; records what it was sent."""
    model = "fake-model"

    def __init__(self, *drafts):
        self.drafts = list(drafts)
        self.calls = []

    def structured(self, *, instructions, document, task, schema):
        self.calls.append({"task": task, "document": document, "schema": schema})
        if schema is JobAnalysis:
            return JobAnalysis(required_skills=["Python", "React", "AWS"], preferred_skills=["Kubernetes"],
                               responsibilities=["Build APIs"], min_years_experience=None, employment_type="full-time",
                               sponsorship="not_mentioned", sponsorship_evidence="",
                               citizenship_or_clearance_required=False, citizenship_evidence="",
                               graduation_requirement=None, work_mode="onsite")
        return self.drafts.pop(0)


@pytest.fixture
def rich_profile(profile):
    profile.experience = [
        Experience(id="acme", title="Software Engineering Intern", organization="Acme", start="2025-05", end="2025-08",
                   bullets=["Built REST APIs in Python serving 2,000 daily users",
                            "Wrote React components for the admin dashboard",
                            "Contributed to migrating CI pipelines to GitHub Actions"],
                   technologies=["Python", "FastAPI", "React", "PostgreSQL"]),
        Experience(id="uni", title="Research Assistant", organization="Example U", start="2024-01", end=None,
                   bullets=["Trained PyTorch models for text classification"], technologies=["PyTorch", "Python"]),
    ]
    profile.projects = [Project(id="bot", name="Course Chatbot", description="RAG chatbot over course notes",
                                technologies=["Python", "AWS"], outcomes=["Used by 40 students in a pilot"])]
    profile.education[0].coursework = ["Compilers", "Cloud Computing", "Machine Learning"]
    return profile


@pytest.fixture
def job(session):
    job, _ = upsert_posting(session, posting(description=DESCRIPTION))
    app = ensure_application(session, job)
    transition(session, app, Status.SHORTLISTED, reason="test")
    return job


def draft(exp_bullets, skills=("Python", "React"), proj=None, summary=None):
    return TailoredDraft(
        summary=summary,
        experiences=[DraftEntry(entry_id="exp:acme", bullets=exp_bullets)],
        projects=proj or [],
        skills=list(skills), matched_requirements=[], gaps=["Kubernetes"], change_summary=["Reordered"])


def test_rules_path_keeps_original_wording_and_master(session, job, rich_profile):
    before = rich_profile.model_dump()
    out = tailor_resume(session, job, rich_profile, llm=None)
    v = out.version
    assert rich_profile.model_dump() == before  # master record untouched
    assert v.version == 1 and v.content_hash == hash_file(v.file_path)
    bullets = [b["text"] for e in v.content["experiences"] for b in e["bullets"]]
    assert set(bullets) <= {b for e in rich_profile.experience for b in e.bullets}
    assert job.application.status == Status.RESUME_READY
    assert job.application.resume_version_id == v.id
    assert v.content["skills"][:2] == ["Python", "React"]
    assert "Kubernetes" not in v.content["skills"]


def test_fabricated_metric_and_tool_are_replaced_with_original(session, job, rich_profile):
    bad = draft([
        DraftBullet(text="Built REST APIs in Python on Kubernetes, cutting latency by 40%", source_fact_ids=["exp:acme:b0"]),
        DraftBullet(text="Wrote React components for the admin dashboard", source_fact_ids=["exp:acme:b1"]),
    ])
    llm = FakeLLM(bad, bad)  # first draft and the repair attempt both fabricate
    out = tailor_resume(session, job, rich_profile, llm=llm)
    acme = next(e for e in out.version.content["experiences"] if e["entry_id"] == "exp:acme")
    texts = [b["text"] for b in acme["bullets"]]
    assert texts[0] == "Built REST APIs in Python serving 2,000 daily users"
    assert out.report["llm_attempts"] == 2
    assert any("40%" in v for v in out.report["initial_violations"])
    assert any("kubernetes" in v for v in out.report["initial_violations"])
    assert out.report["auto_corrections"]
    # The repair prompt told the model exactly what to fix.
    assert "40%" in llm.calls[-1]["task"]


def test_repair_attempt_that_succeeds_is_used(session, job, rich_profile):
    bad = draft([DraftBullet(text="Built APIs used by 10,000 users", source_fact_ids=["exp:acme:b0"])])
    good = draft([DraftBullet(text="Built Python REST APIs serving 2,000 daily users", source_fact_ids=["exp:acme:b0"])])
    out = tailor_resume(session, job, rich_profile, llm=FakeLLM(bad, good))
    assert out.report["auto_corrections"] == []
    acme = next(e for e in out.version.content["experiences"] if e["entry_id"] == "exp:acme")
    assert acme["bullets"][0]["text"].startswith("Built Python REST APIs")


def test_posting_goes_in_untrusted_block_and_facts_in_task(session, job, rich_profile):
    llm = FakeLLM(draft([DraftBullet(text="Wrote React components for the admin dashboard", source_fact_ids=["exp:acme:b1"])]))
    tailor_resume(session, job, rich_profile, llm=llm)
    call = llm.calls[-1]
    assert call["document"] == DESCRIPTION
    assert "[exp:acme:b0]" in call["task"] and DESCRIPTION not in call["task"]


def test_verifier_rejects_cross_entry_citation_and_unverified_skill(rich_profile):
    store = build_fact_store(rich_profile)
    d = draft([DraftBullet(text="Trained PyTorch models for text classification", source_fact_ids=["exp:uni:b0"])],
              skills=["Python", "Kubernetes"])
    issues = [str(v) for v in verify_draft(d, rich_profile, store).violations]
    assert any("different entry" in i for i in issues)
    assert any("Kubernetes" in i for i in issues)


def test_ordinary_words_are_not_mistaken_for_tech(rich_profile):
    store = build_fact_store(rich_profile)
    d = draft([DraftBullet(text="Wrote React components for the admin dashboard to help the go-live rest easy",
                           source_fact_ids=["exp:acme:b1"])])
    assert verify_draft(d, rich_profile, store).ok


def test_inflated_role_needs_review_then_user_approves(session, job, rich_profile):
    inflated = draft([DraftBullet(text="Led the migration of CI pipelines to GitHub Actions",
                                  source_fact_ids=["exp:acme:b2"])])
    out = tailor_resume(session, job, rich_profile, llm=FakeLLM(inflated))
    assert job.application.status == Status.REVIEW_REQUIRED
    assert any("leadership" in c for c in out.report["claims_requiring_review"])
    review_resume(session, out.version, approve=True)
    assert job.application.status == Status.RESUME_READY
    assert out.version.review_status == "APPROVED"


def test_eligibility_review_is_not_cleared_by_tailoring(session, rich_profile):
    job, _ = upsert_posting(session, posting(source_job_id="9", url="https://a.com/9"))
    app = ensure_application(session, job)
    transition(session, app, Status.REVIEW_REQUIRED, reason="Sponsorship unclear")
    with pytest.raises(TailoringError, match="Resolve the open review"):
        tailor_resume(session, job, rich_profile, llm=None)


def test_regeneration_creates_new_version(session, job, rich_profile):
    v1 = tailor_resume(session, job, rich_profile, llm=None).version
    v2 = tailor_resume(session, job, rich_profile, llm=None).version
    assert (v1.version, v2.version) == (1, 2)
    assert job.application.resume_version_id == v2.id
    assert session.query(ResumeVersion).count() == 2
    with pytest.raises(TailoringError, match="current resume"):
        review_resume(session, v1, approve=True)


def test_cannot_tailor_once_application_is_past_resume_stage(session, job, rich_profile):
    tailor_resume(session, job, rich_profile, llm=None)
    transition(session, job.application, Status.APPLICATION_PREPARED, reason="test")
    with pytest.raises(TailoringError, match="APPLICATION_PREPARED"):
        tailor_resume(session, job, rich_profile, llm=None)


def test_tailoring_works_when_job_was_scored_without_llm(session, job, rich_profile):
    from backend.agents.job_discovery import evaluate
    evaluate(session, job, rich_profile, llm=None)  # stores a score with no analysis
    out = tailor_resume(session, job, rich_profile, llm=None)
    assert out.report["quality"]["keyword_coverage"]["covered"]


# ---- ATS ----------------------------------------------------------------------------------------

def test_ats_pass_uses_job_spelling_and_adds_only_verified_skills(session, job, rich_profile):
    from backend.agents.resume_tailor import JobRequirements, ats_optimize

    rich_profile.skills = ["Python", "React.js", "Postgres", "Docker"]
    d = draft([DraftBullet(text="Wrote React components for the admin dashboard", source_fact_ids=["exp:acme:b1"])],
              skills=["Docker", "React.js"])
    reqs = JobRequirements(required=["Python", "React", "PostgreSQL", "Kubernetes"], preferred=["Docker"], responsibilities=[])
    out, notes = ats_optimize(d, rich_profile, reqs)
    assert out.skills == ["Python", "React", "PostgreSQL", "Docker"]  # job order, job spelling, no Kubernetes
    assert any("'React.js' as 'React'" in n for n in notes)
    assert verify_draft(out, rich_profile, build_fact_store(rich_profile), reqs.required).ok


def test_ats_score_explains_gaps(rich_profile):
    from backend.services.ats import ats_score

    d = draft([DraftBullet(text="Built REST APIs in Python serving 2,000 daily users", source_fact_ids=["exp:acme:b0"])],
              skills=["Python", "React"])
    r = ats_score(d, rich_profile, title="Backend Python Engineer", description=DESCRIPTION * 3,
                  required=["Python", "React", "AWS", "Kubernetes"], preferred=["PostgreSQL"])
    assert r["required"]["found"] == ["Python", "React"]
    assert r["required"]["missing_you_have"] == ["AWS"]  # in the profile (project tech) but not on this resume
    assert r["required"]["missing_not_in_profile"] == ["Kubernetes"]
    assert 0 <= r["score"] <= 100 and r["ceiling"] < 65  # Kubernetes can't be covered truthfully


def test_pasted_description_endpoint(session, rich_profile, tmp_path):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    rich_profile.work_authorization.authorized_in_us = None  # eligibility review must not block a pasted resume
    body = {"title": "Software Engineer", "company": "Northwind",
            "description": ("We build APIs in Python and React on AWS. PostgreSQL experience a plus. " * 6)}
    with TestClient(app) as c:
        c.put("/profile", json=rich_profile.model_dump(mode="json"))
        assert c.post("/resumes/from-description", json={**body, "description": "too short"}).status_code == 422
        r1 = c.post("/resumes/from-description", json=body)
        assert r1.status_code == 201, r1.text
        res = r1.json()["resume"]
        assert res["report"]["ats"]["score"] > 0 and "React" in res["content"]["skills"]
        r2 = c.post("/resumes/from-description", json=body)  # same posting -> same job, new version
        assert r2.json()["job_id"] == r1.json()["job_id"] and r2.json()["resume"]["version"] == 2
        job = c.get(f"/jobs/{r1.json()['job_id']}").json()
        assert job["status"] == "REVIEW_REQUIRED"  # eligibility review stays open


def test_ats_ignores_soft_skills_matches_phrases_and_role_title(rich_profile):
    from backend.services.ats import _job_terms, ats_score

    d = draft([DraftBullet(text="Built REST APIs in Python serving 2,000 daily users", source_fact_ids=["exp:acme:b0"])],
              skills=["Python", "React"])
    r = ats_score(d, rich_profile, title="Full Stack Software Engineer, Credit Cards & Banking",
                  description="Credit Card & Banking team. " * 4,
                  required=["React", "React Native", "REST APIs development", "Product sense", "Communication"],
                  preferred=[])
    assert r["soft_skills"] == ["Product sense", "Communication"]
    assert "REST APIs development" not in r["required"]["missing_not_in_profile"]
    assert r["required"]["missing_not_in_profile"] == ["React Native"]
    assert "credit" not in r["title"]["words"] and "card banking" not in _job_terms("Credit Card & Banking. " * 5)


def test_every_job_appears_newest_first(session, job, rich_profile):
    only_acme = draft([DraftBullet(text="Wrote React components for the admin dashboard", source_fact_ids=["exp:acme:b1"])])
    out = tailor_resume(session, job, rich_profile, llm=FakeLLM(only_acme))
    order = [e["entry_id"] for e in out.version.content["experiences"]]
    assert order == ["exp:uni", "exp:acme"]  # the current role comes first
    uni = out.version.content["experiences"][0]["bullets"]
    assert [b["text"] for b in uni] == ["Trained PyTorch models for text classification"]
    assert any("left out" in n for n in out.report["ats_changes"])


def test_job_phrases_are_not_fact_checked_as_technologies(rich_profile):
    store = build_fact_store(rich_profile)
    d = draft([DraftBullet(text="Built REST APIs in Python serving 2,000 daily users", source_fact_ids=["exp:acme:b0"])],
              summary=DraftBullet(text="Engineer focused on backend development with Python and React",
                                  source_fact_ids=["exp:acme:b0", "exp:acme:b1"]))
    assert verify_draft(d, rich_profile, store, ["Backend development", "Product sense", "React"]).ok
    # A real tool from the job that the facts don't support is still caught.
    bad = draft([DraftBullet(text="Built REST APIs in Scala", source_fact_ids=["exp:acme:b0"])])
    assert not verify_draft(bad, rich_profile, store, ["Scala"]).ok
