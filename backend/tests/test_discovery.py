from datetime import timedelta

from backend.agents.job_discovery import run_scan
from backend.models import Application, Job, utcnow
from backend.models.states import Status
from backend.services.normalize import normalize_url
from backend.tests.conftest import FakeAdapter, posting


def statuses(session):
    return {a.job.title: a.status for a in session.query(Application)}


def test_normalize_url_strips_tracking_and_case():
    assert normalize_url("http://WWW.Jobs.Acme.com//1/?utm_source=li&gh_src=abc&id=7") == "https://jobs.acme.com/1?id=7"


def test_repeated_scans_do_not_create_duplicates(session, profile):
    adapter = FakeAdapter([posting()])
    first = run_scan(session, profile, adapters=[adapter])
    second = run_scan(session, profile, adapters=[adapter])
    assert (first.new_jobs, second.new_jobs, second.duplicates) == (1, 0, 1)
    assert session.query(Job).count() == 1


def test_same_job_from_two_sources_is_flagged(session, profile):
    run_scan(session, profile, adapters=[FakeAdapter([
        posting(),
        posting(source="other", source_job_id="zz", url="https://boards.other.com/acme/zz"),
    ])])
    jobs = session.query(Job).order_by(Job.id).all()
    assert jobs[1].possible_duplicate_of == jobs[0].id
    assert jobs[1].application.status == Status.REVIEW_REQUIRED


def test_stale_posting_excluded_and_unverified_not_called_recent(session, profile):
    run_scan(session, profile, adapters=[FakeAdapter([
        posting(source_job_id="old", url="https://a.com/old", title="Software Engineer I",
                posted_at=utcnow() - timedelta(days=3)),
        posting(source_job_id="nodate", url="https://a.com/nodate", title="Data Engineer",
                posted_at=None, freshness_confidence="UNVERIFIED",
                description="Python, SQL, Airflow, AWS, PostgreSQL"),
    ])])
    s = statuses(session)
    assert s["Software Engineer I"] == Status.EXCLUDED
    nodate = session.query(Job).filter_by(source_job_id="nodate").one()
    assert nodate.freshness_confidence == "UNVERIFIED" and nodate.posted_at is None


def test_irrelevant_titles_are_skipped(session, profile):
    report = run_scan(session, profile, adapters=[FakeAdapter([
        posting(title="Account Executive"), posting(source_job_id="2", url="https://a.com/2",
                                                     title="Engineering Manager, Platform")])])
    assert report.skipped_role == 2 and report.new_jobs == 0


def test_no_sponsorship_fails_when_profile_needs_it(session, profile):
    run_scan(session, profile, adapters=[FakeAdapter([
        posting(description="Python and React. We are unable to sponsor visas for this role.")])])
    app = session.query(Application).one()
    assert app.status == Status.EXCLUDED
    assert "sponsorship" in app.events[-1].details.lower()


def test_good_match_is_shortlisted_with_reasons(session, profile):
    run_scan(session, profile, adapters=[FakeAdapter([posting()])])
    job = session.query(Job).one()
    assert job.application.status == Status.SHORTLISTED
    assert any("required skills" in r for r in job.scores[0].match_reasons)


def test_role_keywords_match_whole_words_only():
    from backend.services.scoring import role_family_for
    fams = ["sde", "full_stack", "ai_ml", "data"]
    assert role_family_for("Associate, New Verticals - Fulfillment Strategy & Operations", fams) is None
    assert role_family_for("Senior Settlement Analyst", fams) is None
    assert role_family_for("LLM Engineer", fams) == "ai_ml"
    assert role_family_for("SDE II, Payments", fams) == "sde"
    assert role_family_for("Full-Stack Engineer", fams) == "full_stack"


def test_stale_postings_skip_the_llm(session, profile):
    class CountingLLM:
        model = "fake"
        calls = 0

        def structured(self, **_):
            CountingLLM.calls += 1
            raise RuntimeError("no analysis in this test")

    run_scan(session, profile, llm=CountingLLM(), adapters=[FakeAdapter([
        posting(source_job_id="old", url="https://a.com/old", posted_at=utcnow() - timedelta(days=5)),
        posting(source_job_id="new", url="https://a.com/new", title="Software Engineer II"),
    ])])
    assert CountingLLM.calls == 1  # only the fresh posting was analyzed
