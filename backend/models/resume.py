"""Resume draft schemas. Used for OpenAI structured output, so fields have no defaults."""
from __future__ import annotations

from pydantic import BaseModel, Field


class DraftBullet(BaseModel):
    text: str
    source_fact_ids: list[str] = Field(description="Ids of the verified facts this bullet is based on")


class DraftEntry(BaseModel):
    entry_id: str = Field(description="e.g. 'exp:acme' or 'proj:chatbot'")
    bullets: list[DraftBullet]


class RequirementMatch(BaseModel):
    requirement: str
    fact_ids: list[str]


class TailoredDraft(BaseModel):
    summary: DraftBullet | None = Field(description="Optional 1-2 sentence summary; null if not useful")
    experiences: list[DraftEntry] = Field(description="Selected experiences, most relevant first")
    projects: list[DraftEntry] = Field(description="Selected projects, most relevant first")
    skills: list[str] = Field(description="Verified skills only, most relevant to the job first")
    matched_requirements: list[RequirementMatch]
    gaps: list[str] = Field(description="Job requirements the verified facts do not support")
    change_summary: list[str] = Field(description="Short notes on what was reordered, selected, or reworded")
