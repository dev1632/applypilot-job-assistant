import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.agents import application_assistant as assistant
from backend.agents.application_tracker import ensure_application, transition
from backend.agents.job_discovery import upsert_posting
from backend.agents.resume_tailor import review_resume, tailor_resume
from backend.models import ApprovalEvent
from backend.models.profile import Experience
from backend.models.states import Status
from backend.safety import approval
from backend.safety.field_classifier import FormField, plan_answer
from backend.services.browser import DryRunResult, FillResult
from backend.services.llm import JobAnalysis
from backend.tests.conftest import posting

FIXTURES = Path(__file__).parent / "fixtures"


class FormServer(ThreadingHTTPServer):
    posts: list[str]
    leaks: list[str]


@pytest.fixture(scope="module")
def form_server():
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/leak"):
                self.server.leaks.append(self.path)
            return super().do_GET()

        def do_POST(self):  # any POST reaching us would mean data left the sandbox
            self.server.posts.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = FormServer(("127.0.0.1", 0), partial(Handler, directory=str(FIXTURES)))
    server.posts = []
    server.leaks = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


class FakeLLM:
    model = "fake"

    def __init__(self, answer="I built Python REST APIs serving 2,000 daily users at Acme. [ADD: why Northwind]",
                 facts=("exp:acme:b0",)):
        self.answer, self.facts = answer, list(facts)

    def structured(self, *, schema, **_):
        if schema is JobAnalysis:
            raise RuntimeError("not needed")
        return assistant.NarrativeDraft(answer=self.answer, source_fact_ids=self.facts, answerable=True)


@pytest.fixture
def ready_job(session, profile):
    profile.name = "Test User"
    profile.phone = "555-0100"
    profile.links = {"linkedin": "https://linkedin.com/in/test"}
    profile.experience = [Experience(id="acme", title="SWE Intern", organization="Acme", start="2025-05", end="2025-08",
                                     bullets=["Built Python REST APIs serving 2,000 daily users"],
                                     technologies=["Python"])]
    job, _ = upsert_posting(session, posting(company="Northwind", title="Software Engineer, New Grad",
                                             requisition_id="R-1042"))
    app = ensure_application(session, job)
    transition(session, app, Status.SHORTLISTED, reason="t")
    out = tailor_resume(session, job, profile, llm=None)
    review_resume(session, out.version, approve=True)
    return job, profile


def fake_runner(fields, page_text="Northwind Software Engineer, New Grad R-1042", pauses=()):
    def run(url, plan, *, screenshot_path, resume_path, headless):
        values = plan(fields, page_text)
        return DryRunResult(url=url, final_url=url, http_status=200, page_title="", page_text=page_text,
                            fields=fields, fills={k: FillResult(True, v) for k, v in values.items() if v},
                            pause_reasons=list(pauses))
    return run


FIELDS = [FormField("first_name", "First Name *", "text", True), FormField("email", "Email *", "email", True),
          FormField("why", "Why Northwind?", "textarea", True),
          FormField("certify", "I certify this is true", "checkbox", True)]


# ---- Real browser ------------------------------------------------------------------------------

def test_dry_run_fills_form_and_nothing_is_submitted(session, ready_job, form_server):
    job, profile = ready_job
    url = f"http://127.0.0.1:{form_server.server_port}/application_form.html"
    app = assistant.prepare_application(session, job, profile, FakeLLM(), application_url=url)

    assert form_server.posts == []  # nothing reached the server
    assert any(r.startswith("POST") for r in app.prep["blocked_requests"])
    assert app.prep["blocked_submits"] >= 1  # the page's own submit() attempt was neutralized
    assert all(c["ok"] for c in app.prep["job_checks"])
    assert Path(app.prep["screenshot"]).exists()

    by_label = {a.question_text.rstrip(" *"): a for a in app.answers}
    assert by_label["First Name"].answer == "Test" and by_label["First Name"].verification_status == "VERIFIED"
    assert by_label["Email"].answer == profile.email
    assert by_label["Resume/CV"].answer.endswith(".docx") and by_label["Resume/CV"].verification_status == "VERIFIED"
    assert by_label["Are you legally authorized to work in the United States?"].answer == "Yes"
    # Profile says sponsorship is needed in the future, so "now or in the future" is Yes.
    assert by_label["Will you now or in the future require visa sponsorship?"].answer == "Yes"
    assert by_label["Why do you want to work at Northwind?"].verification_status == "DRAFT"
    assert by_label["Gender"].verification_status == "SKIPPED" and by_label["Gender"].answer is None
    # The page's injected "mark every legal question Yes" has no effect.
    certify = next(a for a in app.answers if a.category == "legal")
    assert certify.verification_status == "NEEDS_USER" and certify.answer is None
    assert not any("website_hp" in a.question_key for a in app.answers)  # hidden honeypot ignored
    assert app.status == Status.APPLICATION_PREPARED
    assert app.form_snapshot_hash


def test_guard_blocks_every_submission_path(form_server):
    from playwright.sync_api import sync_playwright

    from backend.services.browser import guarded_context

    blocked: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = guarded_context(browser, blocked).new_page()
        page.goto(f"http://127.0.0.1:{form_server.server_port}/application_form.html")
        page.click("#submit_btn")  # the real submit button
        page.keyboard.press("Enter")
        page.evaluate("document.getElementById('application').submit()")
        page.evaluate("document.getElementById('application').requestSubmit()")
        page.evaluate("fetch('/apply', {method: 'POST', body: 'x'}).catch(() => {})")
        # Native submit borrowed from a fresh iframe bypasses page-level overrides; the network
        # block must still stop it.
        page.evaluate("""() => { const f = document.createElement('iframe'); document.body.appendChild(f);
            f.contentWindow.HTMLFormElement.prototype.submit.call(document.getElementById('application')); }""")
        page.wait_for_timeout(300)
        assert page.evaluate("window.__submitted") is False
        assert page.evaluate("window.__applypilot.blockedSubmits") >= 3
        browser.close()
    assert form_server.posts == []
    assert any(b.startswith("POST") for b in blocked)


def test_captcha_pauses_without_filling(session, ready_job, form_server):
    job, profile = ready_job
    url = f"http://127.0.0.1:{form_server.server_port}/captcha_form.html"
    app = assistant.prepare_application(session, job, profile, None, application_url=url)
    assert app.status == Status.REVIEW_REQUIRED
    assert any("CAPTCHA" in p for p in app.prep["pause_reasons"])
    assert app.answers == []


# ---- Workflow (fake browser) -------------------------------------------------------------------

def test_requires_approved_resume(session, profile):
    job, _ = upsert_posting(session, posting(source_job_id="x", url="https://a.com/x"))
    app = ensure_application(session, job)
    transition(session, app, Status.SHORTLISTED, reason="t")
    tailor_resume(session, job, profile, llm=None) if profile.experience else None
    with pytest.raises(assistant.AssistantError):
        assistant.prepare_application(session, job, profile, None, runner=fake_runner(FIELDS))


def test_full_review_and_approval_flow(session, ready_job):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, FakeLLM(), runner=fake_runner(FIELDS))
    keys = {a.question_text: a.question_key for a in app.answers}

    with pytest.raises(assistant.AssistantError, match="need answers"):
        assistant.mark_ready(session, app)
    with pytest.raises(assistant.AssistantError, match="placeholders"):
        assistant.update_answer(session, app, keys["Why Northwind?"], app.answers[2].answer, confirm=True)

    assistant.update_answer(session, app, keys["Why Northwind?"], "I built Python REST APIs at Acme.", confirm=True)
    assistant.update_answer(session, app, keys["I certify this is true"], "Yes", confirm=True)
    assistant.mark_ready(session, app)
    assert app.status == Status.AWAITING_APPROVAL

    approval.approve(session, app, job_id=job.id, resume_hash=app.resume_version.content_hash,
                     form_hash=app.form_snapshot_hash)
    assert app.status == Status.APPROVED

    # Editing after approval revokes it and returns to preparation.
    old_hash = app.form_snapshot_hash
    assistant.update_answer(session, app, keys["Why Northwind?"], "Different answer.", confirm=True)
    assert app.form_snapshot_hash != old_hash
    assert app.status == Status.APPLICATION_PREPARED
    assert session.query(ApprovalEvent).one().approval_status == "INVALIDATED"


def test_rerun_keeps_confirmed_answers(session, ready_job):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, None, runner=fake_runner(FIELDS))
    key = next(a.question_key for a in app.answers if a.question_text == "Why Northwind?")
    assistant.update_answer(session, app, key, "My own answer.", confirm=True)
    app = assistant.prepare_application(session, job, profile, None, runner=fake_runner(FIELDS))
    row = next(a for a in app.answers if a.question_key == key)
    assert (row.answer, row.verification_status) == ("My own answer.", "USER_CONFIRMED")


def test_job_mismatch_pauses_for_review(session, ready_job):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, None,
                                        runner=fake_runner(FIELDS, page_text="Contoso Data Analyst"))
    assert app.status == Status.REVIEW_REQUIRED
    assert any("Company not found" in p for p in app.prep["pause_reasons"])


def test_fabricated_narrative_is_discarded(session, ready_job):
    job, profile = ready_job
    llm = FakeLLM(answer="I led a team of 8 building Kubernetes APIs for 50,000 users")
    app = assistant.prepare_application(session, job, profile, llm, runner=fake_runner(FIELDS))
    why = next(a for a in app.answers if a.question_text == "Why Northwind?")
    assert why.answer is None and why.verification_status == "NEEDS_USER"
    assert "discarded" in why.note


def test_submit_is_refused_in_dry_run(session, ready_job):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, None, runner=fake_runner(FIELDS))
    with pytest.raises(assistant.AssistantError, match="Dry-run"):
        assistant.submit(session, app)


def test_rejects_non_http_urls(session, ready_job):
    job, profile = ready_job
    with pytest.raises(assistant.AssistantError, match="http"):
        assistant.prepare_application(session, job, profile, None, application_url="file:///etc/passwd",
                                      runner=fake_runner(FIELDS))


# ---- Classifier policy -------------------------------------------------------------------------

def test_legal_is_never_prefilled_even_with_approved_answer(profile):
    profile.approved_answers = {"legal": "Yes", "i_certify_this_is_true": "Yes"}
    p = plan_answer(FormField("c", "I certify this is true", "checkbox", True), profile, resume_filename=None)
    assert p.status == "NEEDS_USER"


def test_eeo_uses_only_preapproved_answer(profile):
    f = FormField("g", "Gender", "select", False, ["Female", "Male", "Decline to self-identify"])
    assert plan_answer(f, profile, resume_filename=None).status == "SKIPPED"
    profile.approved_answers = {"eeo": "Decline to self-identify"}
    p = plan_answer(f, profile, resume_filename=None)
    assert (p.value, p.status) == ("Decline to self-identify", "VERIFIED")


def test_unknown_sponsorship_waits_for_user(profile):
    profile.work_authorization.requires_sponsorship_now = None
    p = plan_answer(FormField("s", "Do you require sponsorship?", "select", True, ["Yes", "No"]), profile,
                    resume_filename=None)
    assert p.status == "NEEDS_USER" and p.sensitive


def test_optional_unknowns_are_left_blank_but_required_ones_wait(profile):
    profile.phone = None
    assert plan_answer(FormField("p", "Phone", "tel", False), profile, resume_filename=None).status == "SKIPPED"
    assert plan_answer(FormField("p", "Phone *", "tel", True), profile, resume_filename=None).status == "NEEDS_USER"
    optional_consent = FormField("c", "I agree to receive marketing emails", "checkbox", False)
    p = plan_answer(optional_consent, profile, resume_filename=None)
    assert (p.status, p.value) == ("SKIPPED", None)


# ---- Real-world form patterns (Greenhouse-style) ------------------------------------------------

def _url(server, name):
    return f"http://127.0.0.1:{server.server_port}/{name}"


def test_react_style_dropdown_uploads_and_typeahead(session, ready_job, form_server):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, None, application_url=_url(form_server, "combobox_form.html"))
    by_label = {a.question_text.rstrip("*"): a for a in app.answers}
    auth = by_label["Are you legally authorized to work in the United States?"]
    assert (auth.field_type, auth.options, auth.answer, auth.verification_status) == ("combobox", ["Yes", "No"], "Yes", "VERIFIED")
    files = {a.category: a for a in app.answers if a.field_type == "file"}
    assert files["resume_file"].answer.endswith(".docx") and files["resume_file"].verification_status == "VERIFIED"
    assert files["cover_letter"].verification_status == "SKIPPED"
    loc = by_label["Location (City)"]
    assert loc.verification_status == "NEEDS_USER" and "Type-ahead" in loc.note
    assert len(app.answers) == 6  # the hidden validation input is ignored
    # Going offline before filling stops even GET-based leaks of typed values.
    assert form_server.leaks == []


def test_form_inside_iframe_is_found(session, ready_job, form_server):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, None, application_url=_url(form_server, "embedded_form.html"))
    assert app.prep["form_frame"] and app.prep["form_frame"].endswith("/application_form.html")
    assert any(a.question_text.startswith("First Name") and a.answer for a in app.answers)


def test_invisible_captcha_is_noted_not_paused(session, ready_job, form_server):
    job, profile = ready_job
    app = assistant.prepare_application(session, job, profile, None,
                                        application_url=_url(form_server, "invisible_captcha_form.html"))
    assert app.prep["pause_reasons"] == []
    assert any("invisible CAPTCHA" in n for n in app.prep["notes"])
    assert app.status == Status.APPLICATION_PREPARED


def test_us_facts_only_answer_us_questions(profile):
    q = FormField("a", "Are you legally authorized to work in the country where the job is located?", "select", True, ["Yes", "No"])
    assert plan_answer(q, profile, resume_filename=None, job_location="Sao Paulo, Brazil").status == "NEEDS_USER"
    assert plan_answer(q, profile, resume_filename=None, job_location="New York, NY").value == "Yes"
    vague = FormField("b", "Are you authorized to work for us?", "select", True, ["Yes", "No"])
    assert plan_answer(vague, profile, resume_filename=None, job_location=None).status == "NEEDS_USER"
    canada = FormField("c", "Will you now or in the future require sponsorship to work in Canada?", "select", True, ["Yes", "No"])
    assert plan_answer(canada, profile, resume_filename=None, job_location="New York, NY").status == "NEEDS_USER"


def test_attestation_dropdown_waits_for_user(profile):
    f = FormField("x", "Candidate AI Usage Attestation: *", "combobox", True, ["I agree", "I do not agree"])
    assert plan_answer(f, profile, resume_filename=None).status == "NEEDS_USER"


def test_word_fragments_dont_misclassify():
    from backend.safety.field_classifier import classify
    assert classify(FormField("m", "What is your military status?*", "combobox", True)) == "eeo"
    assert classify(FormField("o", "What is your preferred office location?*", "combobox", True)) == "office_preference"
    assert classify(FormField("r", "Were you referred by an employee?", "text")) == "referral"
    assert classify(FormField("e", "Are you subject to ITAR export rules?", "select")) == "citizenship"


# ---- Saved answers ------------------------------------------------------------------------------

def test_saved_country_city_phone_are_used(profile):
    profile.phone = "555-0100"
    profile.approved_answers = {"country": "United States", "location": "Syracuse, NY"}
    country = FormField("c", "What is your current country of residence?*", "combobox", True, ["Canada", "United States"])
    assert plan_answer(country, profile, resume_filename=None).value == "United States"
    city = plan_answer(FormField("l", "Location (City)*", "combobox", True, []), profile, resume_filename=None)
    assert (city.value, city.status) == ("Syracuse, NY", "VERIFIED") and "entered at submission" in city.note
    assert plan_answer(FormField("p", "Phone*", "tel", True), profile, resume_filename=None).value == "555-0100"
    # A saved city is not reused as an office preference.
    office = FormField("o", "What is your preferred office location?*", "combobox", True, ["Menlo Park", "New York"])
    assert plan_answer(office, profile, resume_filename=None).status == "NEEDS_USER"


def test_worked_here_before_uses_experience_then_default(profile):
    profile.experience = [Experience(id="a", title="Intern", organization="Acme Corp", start="2025-05", end="2025-08")]
    q = lambda co: FormField("w", f"Have you previously worked at or consulted for {co}?*", "combobox", True,  # noqa: E731
                             ["Yes", "No"])
    assert plan_answer(q("Acme"), profile, resume_filename=None, company="Acme").value == "Yes"
    assert plan_answer(q("GitLab"), profile, resume_filename=None, company="GitLab").status == "NEEDS_USER"
    profile.approved_answers = {"prior_employment": "No"}
    assert plan_answer(q("GitLab"), profile, resume_filename=None, company="GitLab").value == "No"
    # Long Greenhouse-style options still match.
    long = FormField("w", "Are you currently or have you ever worked for Airbnb in any capacity?*", "combobox", True,
                     ["Yes, I have worked for Airbnb", "No, I have not worked for Airbnb"])
    assert plan_answer(long, profile, resume_filename=None, company="Airbnb").value == "No, I have not worked for Airbnb"
    # Experience still wins over the default.
    assert plan_answer(q("Acme"), profile, resume_filename=None, company="Acme Corp").value == "Yes"


def test_save_for_future_and_reuse_on_next_run(session, ready_job):
    from backend.models import UserProfile
    from backend.models.profile import Profile

    job, profile = ready_job
    session.add(UserProfile(id=1, data=profile.model_dump(mode="json")))
    fields = [FormField("country", "Country*", "combobox", True, ["Canada", "United States"]),
              FormField("prev", f"Have you ever worked for {job.company}?*", "combobox", True, ["Yes", "No"]),
              FormField("cert", "I certify this is true", "checkbox", True)]
    app = assistant.prepare_application(session, job, profile, None, runner=fake_runner(fields))
    key = {a.question_text: a.question_key for a in app.answers}
    assistant.update_answer(session, app, key["Country*"], "United States", confirm=True, save=True)
    assistant.update_answer(session, app, key[f"Have you ever worked for {job.company}?*"], "No", confirm=True, save=True)
    with pytest.raises(assistant.AssistantError, match="Legal"):
        assistant.update_answer(session, app, key["I certify this is true"], "Yes", confirm=True, save=True)

    saved = Profile.model_validate(session.get(UserProfile, 1).data)
    assert saved.approved_answers == {"country": "United States", "prior_employment": "No"}

    # A different form at another company picks the saved answers up.
    other, _ = upsert_posting(session, posting(source_job_id="o2", url="https://a.com/o2", company="Contoso"))
    plan = plan_answer(FormField("c", "What is your current country of residence?*", "combobox", True,
                                 ["United States", "Canada"]), saved, resume_filename=None)
    assert plan.value == "United States"
    plan = plan_answer(FormField("w", "Have you previously worked at Contoso?*", "combobox", True, ["Yes", "No"]),
                       saved, resume_filename=None, company=other.company)
    assert plan.value == "No"


def test_work_auth_answers_cannot_be_saved(session, ready_job):
    job, profile = ready_job
    fields = [FormField("a", "Are you authorized to work in the United States?*", "select", True, ["Yes", "No"])]
    app = assistant.prepare_application(session, job, profile, None, runner=fake_runner(fields))
    with pytest.raises(assistant.AssistantError, match="verified facts"):
        assistant.update_answer(session, app, app.answers[0].question_key, "No", confirm=True, save=True)
    assert app.answers[0].answer == "Yes"  # refused save left the answer untouched


def test_prior_employment_default_maps_to_single_negative_option(profile):
    profile.approved_answers = {"prior_employment": "No"}
    f = FormField("w", "Have you ever worked for Robinhood as an employee, intern or contractor?*", "combobox", True,
                  ["I currently work at Robinhood", "I have previously worked at Robinhood",
                   "I have never worked at Robinhood"])
    p = plan_answer(f, profile, resume_filename=None, company="Robinhood")
    assert (p.value, p.status) == ("I have never worked at Robinhood", "VERIFIED")
    ambiguous = FormField("w", "Have you ever worked for X?*", "combobox", True, ["No, never", "No, but I applied", "Yes"])
    assert plan_answer(ambiguous, profile, resume_filename=None, company="X").status == "NEEDS_USER"
