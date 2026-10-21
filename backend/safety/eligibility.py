"""Eligibility screening (design doc section 6).

Outcomes: PASS (requirements have supporting evidence), REVIEW (missing/ambiguous), FAIL (a
verified hard requirement is not met). These are screening controls, not legal determinations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from backend.models.profile import Profile
from backend.services.llm import JobAnalysis


class Outcome(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


@dataclass
class EligibilityResult:
    outcome: Outcome = Outcome.PASS
    notes: list[str] = field(default_factory=list)

    def flag(self, outcome: Outcome, note: str) -> None:
        self.notes.append(f"{outcome}: {note}")
        rank = {Outcome.PASS: 0, Outcome.REVIEW: 1, Outcome.FAIL: 2}
        if rank[outcome] > rank[self.outcome]:
            self.outcome = outcome


NO_SPONSOR = re.compile(
    r"(unable|not able|cannot|can't|will not|won't|do(es)? not|no)\s+(to\s+)?(provide\s+|offer\s+)?"
    r"(visa\s+)?sponsor(ship)?|without (the need for )?(visa )?sponsorship|sponsorship is not available", re.I)
SPONSOR_OFFERED = re.compile(r"(visa )?sponsorship (is )?(available|offered|provided)|will sponsor", re.I)
CITIZEN_REQ = re.compile(r"(u\.?s\.? citizen(ship)? (is )?required|must be a u\.?s\.? citizen|"
                         r"security clearance|active clearance|itar|export control)", re.I)
GRAD_YEAR = re.compile(r"graduat\w*[^.\n]{0,60}?\b(20\d\d)\b", re.I)
EMPLOYMENT = {
    "internship": re.compile(r"\bintern(ship)?\b|co-?op", re.I),
    "contract": re.compile(r"\bcontract(or)?\b|\b1099\b|\bc2c\b", re.I),
    "part-time": re.compile(r"\bpart[- ]time\b", re.I),
}


def _quote_ok(evidence: str, description: str) -> bool:
    """An LLM claim counts only if its quote really appears in the posting."""
    norm = lambda s: " ".join(s.lower().split())  # noqa: E731
    return bool(evidence) and norm(evidence)[:120] in norm(description)


def screen(profile: Profile, *, title: str, description: str, location: str | None,
           employment_type: str | None, analysis: JobAnalysis | None = None) -> EligibilityResult:
    result = EligibilityResult()
    auth = profile.work_authorization
    text = description or ""
    prefs = profile.preferences

    # --- Sponsorship: explicit statements only; silence is "unknown", not "no".
    if analysis and analysis.sponsorship != "not_mentioned" and _quote_ok(analysis.sponsorship_evidence, text):
        sponsorship = analysis.sponsorship
    elif NO_SPONSOR.search(text):
        sponsorship = "not_offered"
    elif SPONSOR_OFFERED.search(text):
        sponsorship = "offered"
    else:
        sponsorship = "not_mentioned"

    needs = auth.requires_sponsorship_now or auth.requires_sponsorship_future
    if sponsorship == "not_offered":
        if needs is True:
            result.flag(Outcome.FAIL, "Posting states no visa sponsorship; profile says sponsorship is required")
        elif needs is None:
            result.flag(Outcome.REVIEW, "Posting states no sponsorship; your sponsorship need is not recorded")
        else:
            result.notes.append("Sponsorship: not offered (not needed per profile)")
    else:
        result.notes.append(f"Sponsorship: {sponsorship.replace('_', ' ')}")

    # --- Citizenship / clearance
    citizen_req = (analysis.citizenship_or_clearance_required and _quote_ok(analysis.citizenship_evidence, text)) \
        if analysis else bool(CITIZEN_REQ.search(text))
    if citizen_req:
        if auth.us_citizen is False and not auth.has_security_clearance:
            result.flag(Outcome.FAIL, "Posting requires U.S. citizenship or security clearance")
        elif auth.us_citizen is None:
            result.flag(Outcome.REVIEW, "Posting mentions citizenship/clearance; status not recorded in profile")

    if auth.authorized_in_us is None:
        result.flag(Outcome.REVIEW, "Work authorization is not recorded in your profile")

    # --- Employment type
    detected = employment_type
    if analysis and analysis.employment_type != "unknown":
        detected = analysis.employment_type
    if not detected:
        haystack = f"{title}\n{employment_type or ''}"
        detected = next((k for k, rx in EMPLOYMENT.items() if rx.search(haystack)), "full-time")
    if detected.lower() not in [t.lower() for t in prefs.employment_types]:
        result.flag(Outcome.FAIL, f"Employment type '{detected}' is not in your preferences")

    # --- Graduation window
    grad = profile.expected_graduation
    if grad:
        grad_year = int(grad[:4])
        years: set[int] = set()
        if analysis and analysis.graduation_requirement and _quote_ok(analysis.graduation_requirement.evidence, text):
            years = {int(y) for y in re.findall(r"20\d\d", analysis.graduation_requirement.value)}
        else:
            years = {int(m.group(1)) for m in GRAD_YEAR.finditer(text)}
        if years and grad_year not in years:
            result.flag(Outcome.REVIEW, f"Posting mentions graduation year(s) {sorted(years)}; "
                                        f"your expected graduation is {grad}")

    # --- Location
    loc = (location or "").lower()
    remote = "remote" in loc or (analysis is not None and analysis.work_mode == "remote")
    if prefs.locations and not (remote and prefs.remote_ok):
        if not any(p.lower() in loc for p in prefs.locations):
            result.flag(Outcome.REVIEW, f"Location '{location}' does not match preferred locations")

    return result
