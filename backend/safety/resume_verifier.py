"""Deterministic checks that a tailored resume only states verified facts.

Hard violations (the draft must not be used as-is):
  * unknown entry or fact ids, or a bullet citing facts from a different entry
  * numbers (metrics, percentages, counts, years) not present in the cited facts
  * technologies not present in the cited facts / that entry's verified technologies
  * skills that are not in the verified profile
Soft flags (claims requiring user review):
  * leadership/ownership wording not supported by the sources
  * bullets that share little wording with their sources
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models.profile import Profile
from backend.models.resume import DraftBullet, TailoredDraft
from backend.services.facts import Fact, FactStore, verified_technologies
from backend.services.scoring import SKILL_VOCAB, _mentions, canon

NUMBER = re.compile(r"(?<![a-z0-9])\$?\d[\d,]*(?:\.\d+)?(?:\s?%|(?:x|k|m|b)(?![a-z])|\+)?", re.I)
# Tech names that are also ordinary English words only count when written in their usual casing.
AMBIGUOUS = {"go": re.compile(r"\bGo\b"), "rest": re.compile(r"\bREST(ful)?\b"),
             "spring": re.compile(r"\bSpring\b(?! (semester|term|20\d\d))")}
LEADERSHIP = re.compile(r"\b(led|lead|leading|managed|manager|owned|spearheaded|mentored|architected|"
                        r"directed|headed|founded|supervised)\b", re.I)
STOP = set("a an the and or of to in for on with by at from as using via into over is was were be "
           "that this which their its our my".split())


@dataclass
class Issue:
    where: str
    message: str
    fact_ids: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class VerificationResult:
    violations: list[Issue] = field(default_factory=list)
    review_claims: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[,\s$]", "", m.group()).lower().rstrip("+") for m in NUMBER.finditer(text)}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]*", text.lower()) if w not in STOP and len(w) > 2}


def _is_tool_name(term: str) -> bool:
    """Job 'skills' like 'Backend development' or 'Product sense' are phrases, not technologies to
    fact-check; only short tool-like names (Scala, React Native, CI/CD) are."""
    from backend.services.ats import FILLER, SOFT

    words = term.lower().split()
    return 0 < len(words) <= 3 and not SOFT.search(term) and not any(w in FILLER for w in words)


def tech_vocabulary(profile: Profile, extra: list[str] = ()) -> set[str]:
    vocab = {canon(s) for s in SKILL_VOCAB} | verified_technologies(profile)
    return vocab | {canon(s) for s in extra if s and len(s) <= 40 and _is_tool_name(s)}


def check_bullet(bullet: DraftBullet, where: str, entry_id: str | None, store: FactStore,
                 vocab: set[str], result: VerificationResult) -> None:
    sources: list[Fact] = []
    for fid in bullet.source_fact_ids:
        fact = store.get(fid)
        if fact is None:
            result.violations.append(Issue(where, f"cites unknown fact '{fid}'"))
        elif entry_id is not None and fact.entry_id not in (entry_id, "profile"):
            result.violations.append(Issue(where, f"cites fact '{fid}' from a different entry"))
        else:
            sources.append(fact)
    if not sources:
        result.violations.append(Issue(where, "has no valid source facts"))
        return

    src_text = " ".join(f.text for f in sources)
    allowed_numbers = _numbers(src_text)
    for num in _numbers(bullet.text) - allowed_numbers:
        result.violations.append(Issue(where, f"states '{num}', which is not in the cited facts",
                                       bullet.source_fact_ids))

    allowed_tech = {canon(t) for f in sources for t in f.technologies}
    low_src = src_text.lower()
    low = bullet.text.lower()
    for tech in vocab:
        if tech in AMBIGUOUS and not AMBIGUOUS[tech].search(bullet.text):
            continue
        if _mentions(tech, low) and tech not in allowed_tech and not _mentions(tech, low_src):
            result.violations.append(Issue(where, f"mentions '{tech}', which the cited facts do not support",
                                           bullet.source_fact_ids))

    if LEADERSHIP.search(bullet.text) and not LEADERSHIP.search(src_text):
        result.review_claims.append(Issue(where, "uses leadership/ownership wording not in the sources",
                                          bullet.source_fact_ids))
    words = _words(bullet.text)
    if words and len(words & _words(src_text)) / len(words) < 0.4:
        result.review_claims.append(Issue(where, "is worded quite differently from its sources; confirm accuracy",
                                          bullet.source_fact_ids))


def verify_draft(draft: TailoredDraft, profile: Profile, store: FactStore,
                 job_skills: list[str] = ()) -> VerificationResult:
    result = VerificationResult()
    vocab = tech_vocabulary(profile, list(job_skills))
    known_entries = {f"exp:{e.id}" for e in profile.experience} | {f"proj:{p.id}" for p in profile.projects}

    if draft.summary:
        check_bullet(draft.summary, "summary", None, store, vocab, result)

    for section, entries, prefix in (("experience", draft.experiences, "exp:"),
                                     ("project", draft.projects, "proj:")):
        seen: set[str] = set()
        for entry in entries:
            if entry.entry_id not in known_entries or not entry.entry_id.startswith(prefix):
                result.violations.append(Issue(section, f"unknown {section} '{entry.entry_id}'"))
                continue
            if entry.entry_id in seen:
                result.violations.append(Issue(section, f"'{entry.entry_id}' appears twice"))
            seen.add(entry.entry_id)
            for i, bullet in enumerate(entry.bullets):
                check_bullet(bullet, f"{entry.entry_id} bullet {i + 1}", entry.entry_id, store, vocab, result)

    verified = {canon(s) for s in profile.skills} | verified_technologies(profile)
    for skill in draft.skills:
        if canon(skill) not in verified:
            result.violations.append(Issue("skills", f"'{skill}' is not a verified skill"))
    return result


# ---- Quality checks (reported, not blocking) ------------------------------------------------

def quality_checks(profile: Profile, draft: TailoredDraft, job_required: list[str]) -> dict:
    texts = [b.text for e in draft.experiences + draft.projects for b in e.bullets]
    if draft.summary:
        texts.append(draft.summary.text)
    full = " ".join(texts + draft.skills).lower()

    covered = [s for s in job_required if _mentions(canon(s), full)]
    formatting: list[str] = []
    for t in texts:
        if len(t) > 260:
            formatting.append(f"Long bullet ({len(t)} chars): {t[:50]}…")
        if t and not t[0].isupper():
            formatting.append(f"Bullet should start with a capital letter: {t[:50]}")
        if re.search(r"\b(\w+) \1\b", t, re.I):
            formatting.append(f"Repeated word: {t[:50]}")
        if "  " in t:
            formatting.append(f"Double space: {t[:50]}")
    if len(set(texts)) != len(texts):
        formatting.append("Duplicate bullets")
    endings = {t.rstrip()[-1:] == "." for t in texts if t}
    if len(endings) > 1:
        formatting.append("Inconsistent trailing periods across bullets")

    return {
        "keyword_coverage": {"covered": covered, "missing": [s for s in job_required if s not in covered],
                             "ratio": round(len(covered) / len(job_required), 2) if job_required else None},
        "chronology": chronology_issues(profile),
        "formatting": formatting,
    }


def chronology_issues(profile: Profile) -> list[str]:
    issues = []
    for exp in profile.experience:
        if exp.end and exp.end < exp.start:
            issues.append(f"{exp.title} at {exp.organization}: end {exp.end} is before start {exp.start}")
        if not re.fullmatch(r"\d{4}-\d{2}", exp.start) or (exp.end and not re.fullmatch(r"\d{4}-\d{2}", exp.end)):
            issues.append(f"{exp.title} at {exp.organization}: dates should be YYYY-MM")
    return issues
