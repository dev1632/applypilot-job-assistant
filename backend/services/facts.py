"""Verified fact store built from the user's profile.

The profile is the master record and is never modified by tailoring. Each resume bullet the system
writes must cite fact ids from this store, so every claim can be audited back to a source.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.models.profile import Profile


@dataclass(frozen=True)
class Fact:
    id: str
    entry_id: str  # "exp:<id>", "proj:<id>", or "profile"
    kind: str  # bullet | description | outcome | skill | coursework
    text: str
    technologies: tuple[str, ...] = ()


@dataclass
class FactStore:
    facts: dict[str, Fact] = field(default_factory=dict)

    def add(self, fact: Fact) -> None:
        self.facts[fact.id] = fact

    def for_entry(self, entry_id: str) -> list[Fact]:
        return [f for f in self.facts.values() if f.entry_id == entry_id]

    def get(self, fact_id: str) -> Fact | None:
        return self.facts.get(fact_id)

    def as_prompt(self, profile: Profile) -> str:
        """Compact, trusted listing of facts for the model."""
        lines: list[str] = []
        for exp in profile.experience:
            lines.append(f"## exp:{exp.id} | {exp.title} at {exp.organization} | {exp.start} to {exp.end or 'present'}"
                         f" | technologies: {', '.join(exp.technologies) or 'none listed'}")
            lines += [f"- [{f.id}] {f.text}" for f in self.for_entry(f"exp:{exp.id}")]
        for proj in profile.projects:
            lines.append(f"## proj:{proj.id} | {proj.name} | technologies: {', '.join(proj.technologies) or 'none listed'}")
            lines += [f"- [{f.id}] {f.text}" for f in self.for_entry(f"proj:{proj.id}")]
        lines.append(f"## Verified skills: {', '.join(profile.skills)}")
        return "\n".join(lines)


def build_fact_store(profile: Profile) -> FactStore:
    store = FactStore()
    for exp in profile.experience:
        entry = f"exp:{exp.id}"
        for i, bullet in enumerate(exp.bullets):
            store.add(Fact(f"{entry}:b{i}", entry, "bullet", bullet, tuple(exp.technologies)))
    for proj in profile.projects:
        entry = f"proj:{proj.id}"
        store.add(Fact(f"{entry}:desc", entry, "description", proj.description, tuple(proj.technologies)))
        for i, outcome in enumerate(proj.outcomes):
            store.add(Fact(f"{entry}:out{i}", entry, "outcome", outcome, tuple(proj.technologies)))
    for skill in profile.skills:
        store.add(Fact(f"skill:{skill}", "profile", "skill", skill, (skill,)))
    for ei, edu in enumerate(profile.education):
        for ci, course in enumerate(edu.coursework):
            store.add(Fact(f"edu:{ei}:c{ci}", "profile", "coursework", course))
    return store


def verified_technologies(profile: Profile) -> set[str]:
    from backend.services.scoring import canon

    techs = {canon(s) for s in profile.skills}
    for exp in profile.experience:
        techs |= {canon(t) for t in exp.technologies}
    for proj in profile.projects:
        techs |= {canon(t) for t in proj.technologies}
    return techs
