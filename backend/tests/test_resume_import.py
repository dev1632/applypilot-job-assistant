import io

import pytest
from docx import Document

from backend.models.profile import Experience, Profile
from backend.services import resume_import as ri

RESUME_LINES = [
    "Jordan Lee",
    "jordan@example.com | (315) 555-0142 | linkedin.com/in/jordanlee | github.com/jlee",
    "EXPERIENCE",
    "Software Engineering Intern, Acme Corp — May 2025 to Aug 2025",
    "• Built REST APIs in Python serving 2,000 daily users",
    "• Wrote React components for the admin dashboard",
    "PROJECTS",
    "Course Chatbot: Retrieval chatbot over course notes",
    "• Used by 40 students in a pilot",
    "SKILLS",
    "Languages: Python, TypeScript, SQL",
]


def docx_bytes(lines=RESUME_LINES) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class FakeLLM:
    model = "fake"

    def structured(self, *, schema, **_):
        return ri.ImportedProfile(
            name="Jordan Lee", email="jordan@example.com", phone="(315) 555-0142",
            links=[ri.ImpLink(label="linkedin", url="https://linkedin.com/in/jordanlee")],
            skills=["Python", "TypeScript", "SQL", "Kubernetes"],  # Kubernetes isn't on the resume
            education=[],
            experience=[ri.ImpExperience(
                title="Software Engineering Intern", organization="Acme Corp", start="2025-05", end="2025-08",
                current=False, technologies=["Python", "React", "Kafka"],  # Kafka isn't on the resume
                bullets=["Built REST APIs in Python serving 2,000 daily users",
                         "Engineered scalable React dashboards used by 10,000 admins"])],  # reworded + new number
            projects=[ri.ImpProject(name="Course Chatbot", description="Retrieval chatbot over course notes",
                                    technologies=[], outcomes=["Used by 40 students in a pilot"])])


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from backend.config import get_settings
    monkeypatch.setenv("APPLYPILOT_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield tmp_path / "data"
    get_settings.cache_clear()


def test_upload_validation(data_dir):
    with pytest.raises(ri.ImportError_, match="docx, .pdf"):
        ri.store_upload(b"x", "resume.exe")
    with pytest.raises(ri.ImportError_, match="real .pdf"):
        ri.store_upload(b"not a pdf at all", "resume.pdf")
    with pytest.raises(ri.ImportError_, match="5 MB"):
        ri.store_upload(b"PK\x03\x04" + b"0" * ri.MAX_BYTES, "resume.docx")
    path = ri.store_upload(docx_bytes(), "../../etc/My Resume!.docx")
    assert path.parent == data_dir / "master_resume" and ".." not in path.name
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_model_draft_is_checked_against_the_resume(data_dir):
    record = ri.create_import(docx_bytes(), "resume.docx", FakeLLM())
    assert record["method"] == "openai"
    exp = record["experience"][0]
    kept, reworded = exp["bullets"]
    assert kept["verbatim"] is True
    assert reworded["verbatim"] is False
    assert any("word for word" in f for f in reworded["flags"])
    assert any("10,000" in f for f in reworded["flags"])
    assert {t["value"]: t["found"] for t in exp["technologies"]} == {"Python": True, "React": True, "Kafka": False}
    assert {s["value"]: s["found"] for s in record["skills"]}["Kubernetes"] is False
    assert all(c["found"] for c in record["contact"])
    assert record["projects"][0]["outcomes"][0]["verbatim"] is True


def test_rules_fallback_reads_contact_and_skills(data_dir):
    record = ri.create_import(docx_bytes(), "resume.docx", None)
    assert record["method"] == "rules"
    fields = {c["field"]: c["value"] for c in record["contact"]}
    assert fields["email"] == "jordan@example.com" and fields["name"] == "Jordan Lee"
    assert "links.linkedin" in fields and "links.github" in fields
    assert [s["value"] for s in record["skills"]] == ["Python", "TypeScript", "SQL"]
    assert record["experience"] == [] and record["warnings"]


def test_apply_merges_only_picked_items_and_never_overwrites(data_dir):
    record = ri.create_import(docx_bytes(), "resume.docx", FakeLLM())
    profile = Profile(name="Jordan Lee", email="old@example.com", skills=["Python"],
                      experience=[Experience(id="old", title="TA", organization="Example U", start="2024-01",
                                             bullets=["Graded labs"])])
    before = profile.model_dump()
    pick = ri.ImportSelection(contact=["phone"], skills=["TypeScript", "Python"],
                              experience=[ri.ExperiencePick(id="exp0", start="2025-05", end="2025-08", bullets=[0])],
                              projects=[ri.ProjectPick(id="proj0", outcomes=[0])])
    merged, log = ri.apply_import(profile, record, pick)
    assert profile.model_dump() == before  # input untouched
    assert merged.email == "old@example.com" and merged.phone == "(315) 555-0142"
    assert merged.skills == ["Python", "TypeScript"]
    new = merged.experience[1]
    assert new.bullets == ["Built REST APIs in Python serving 2,000 daily users"]
    assert new.technologies == ["Python", "React"]  # unverified Kafka dropped
    assert merged.experience[0].bullets == ["Graded labs"]
    assert merged.projects[0].outcomes == ["Used by 40 students in a pilot"]
    # Importing the same role again is skipped rather than duplicated.
    again, log2 = ri.apply_import(merged, record, pick)
    assert len(again.experience) == 2 and any("already in profile" in line for line in log2)
    assert not any("different" in line for line in log2)  # identical re-import is a plain skip


def test_apply_requires_valid_dates(data_dir):
    record = ri.create_import(docx_bytes(), "resume.docx", FakeLLM())
    with pytest.raises(ri.ImportError_, match="YYYY-MM"):
        ri.apply_import(Profile(), record, ri.ImportSelection(
            experience=[ri.ExperiencePick(id="exp0", start="2025", bullets=[0])]))


def test_upload_endpoint_end_to_end(data_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app
    from backend.db import init_db

    init_db(f"sqlite:///{data_dir.parent / 'api.db'}")
    with TestClient(app) as c:
        bad = c.post("/profile/imports", files={"file": ("cv.pdf", b"hello", "application/pdf")})
        assert bad.status_code == 422
        r = c.post("/profile/imports", files={"file": ("cv.docx", docx_bytes(), "application/octet-stream")})
        assert r.status_code == 201
        rid = r.json()["id"]
        assert c.get(f"/profile/imports/{rid}").json()["id"] == rid
        assert c.get("/profile/imports/../../etc").status_code == 404
        applied = c.post(f"/profile/imports/{rid}/apply", json={"contact": ["email", "name"], "skills": ["SQL"]}).json()
        assert applied["profile"]["email"] == "jordan@example.com" and applied["profile"]["skills"] == ["SQL"]
        assert c.get("/profile").json()["email"] == "jordan@example.com"


def test_graduation_is_end_of_range_and_wrapped_bullets_rejoin():
    text = ("EDUCATION\nMaster of Science in Computer Science, Syracuse University Jan 2025 – Dec 2026\n"
            "Bachelor of Engineering, Pune University Aug 2020 – May 2024\n")
    draft = ri.ImportedProfile(
        name="A", email=None, phone=None, links=[], skills=[],
        education=[ri.ImpEducation(school="Syracuse University", degree="MS", field="CS", graduation="2025-01", coursework=[]),
                   ri.ImpEducation(school="Pune University", degree="BE", field="IT", graduation="2024-05", coursework=[])],
        experience=[ri.ImpExperience(title="SWE", organization="X", start="2024-01", end=None, current=True, technologies=[],
                                     bullets=["Built services in Go and", "Python for payments.", "Wrote tests."])],
        projects=[ri.ImpProject(name="Tracker", description="Built a finance platform delivering real-time",
                                technologies=[], outcomes=["tracking across 3 workflows.",
                                                           "Engineered a pipeline using Python, automating",
                                                           "receipt processing with CI/CD."])])
    fixed, notes = ri.repair(draft, text)
    assert [e.graduation for e in fixed.education] == ["2026-12", "2024-05"]
    assert len(notes) == 1 and "2026-12" in notes[0]
    assert fixed.experience[0].bullets == ["Built services in Go and Python for payments.", "Wrote tests."]
    assert fixed.projects[0].description == "Built a finance platform delivering real-time tracking across 3 workflows."
    assert fixed.projects[0].outcomes == ["Engineered a pipeline using Python, automating receipt processing with CI/CD."]
    # Rejoined bullets still pass the word-for-word check against wrapped text.
    wrapped = text + "Built services in Go and\nPython for payments.\n"
    assert ri.annotate(fixed, wrapped, "openai")["experience"][0]["bullets"][0]["verbatim"]


def test_cut_off_bullet_is_completed_from_resume_text():
    text = ("PROJECTS\nModel Card Docs\n"
            "• Contributed to Transformers (142k stars) by enhancing the GPT\n"
            "model card with runnable examples for pipeline and CLI usage.\n"
            "• Added 3 usage examples covering pipeline and\n"
            "CLI workflows, improving onboarding.\n")
    draft = ri.ImportedProfile(name="A", email=None, phone=None, links=[], skills=[], education=[], experience=[],
                               projects=[ri.ImpProject(name="Model Card Docs", technologies=[],
                                                       description="Contributed to Transformers (142k stars) by enhancing the GPT",
                                                       outcomes=["Added 3 usage examples covering pipeline and",
                                                                 "CLI workflows, improving onboarding."])])
    fixed, _ = ri.repair(draft, text)
    p = fixed.projects[0]
    assert p.description.endswith("model card with runnable examples for pipeline and CLI usage.")
    assert p.outcomes == ["Added 3 usage examples covering pipeline and CLI workflows, improving onboarding."]
    a = ri.annotate(fixed, text, "openai")["projects"][0]
    assert a["description"]["verbatim"] and a["outcomes"][0]["verbatim"]


def test_reimport_with_changed_content_says_so(data_dir):
    record = ri.create_import(docx_bytes(), "resume.docx", FakeLLM())
    profile = Profile(experience=[Experience(id="acme", title="Software Engineering Intern", organization="Acme Corp",
                                             start="2025-05", end="2025-08", bullets=["Old wording"])])
    _, log = ri.apply_import(profile, record, ri.ImportSelection(
        experience=[ri.ExperiencePick(id="exp0", start="2025-05", end="2025-08", bullets=[0])]))
    assert any("different content" in line for line in log)
