"""Pydantic schema for the verified career profile and search preferences."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

ROLE_FAMILIES: dict[str, list[str]] = {
    "sde": ["software engineer", "software development engineer", "sde", "software developer",
            "backend engineer", "frontend engineer"],
    "full_stack": ["full stack", "full-stack", "fullstack"],
    "ai_ml": ["machine learning", "ml engineer", "ai engineer", "applied scientist", "deep learning",
              "llm", "artificial intelligence"],
    "data": ["data engineer", "analytics engineer", "data platform", "etl"],
}


class WorkAuthorization(BaseModel):
    """User-verified facts only. None means 'not provided', which routes to manual review."""
    authorized_in_us: bool | None = None
    requires_sponsorship_now: bool | None = None
    requires_sponsorship_future: bool | None = None
    us_citizen: bool | None = None
    has_security_clearance: bool | None = None
    notes: str | None = None


class Experience(BaseModel):
    id: str
    title: str
    organization: str
    start: str  # YYYY-MM
    end: str | None = None
    bullets: list[str] = []
    technologies: list[str] = []


class Project(BaseModel):
    id: str
    name: str
    description: str
    technologies: list[str] = []
    outcomes: list[str] = []  # only documented, evidenced outcomes


class Education(BaseModel):
    school: str
    degree: str
    field: str
    expected_graduation: str  # YYYY-MM, e.g. 2026-12
    coursework: list[str] = []


class SourceConfig(BaseModel):
    type: str  # greenhouse | lever
    board: str  # board token / company handle
    company: str | None = None


class Preferences(BaseModel):
    role_families: list[str] = Field(default_factory=lambda: list(ROLE_FAMILIES))
    locations: list[str] = []  # substrings, e.g. "New York", "Remote"
    remote_ok: bool = True
    employment_types: list[str] = ["full-time"]
    earliest_start: date | None = None
    min_skills_score: float = 0.25
    sources: list[SourceConfig] = []
    title_exclude: list[str] = ["staff", "senior", "sr.", "principal", "director", "manager", "lead", "head of"]


class Profile(BaseModel):
    name: str = ""
    email: str = ""
    phone: str | None = None
    links: dict[str, str] = {}
    skills: list[str] = []
    education: list[Education] = []
    experience: list[Experience] = []
    projects: list[Project] = []
    work_authorization: WorkAuthorization = WorkAuthorization()
    preferences: Preferences = Preferences()
    approved_answers: dict[str, str] = {}  # question_key -> user-approved answer

    @property
    def expected_graduation(self) -> str | None:
        return self.education[0].expected_graduation if self.education else None
