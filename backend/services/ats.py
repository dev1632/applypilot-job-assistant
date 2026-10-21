"""ATS match score for a tailored resume against one job description.

Applicant tracking systems mostly rank by keyword matches, then title alignment and parseable
formatting. This score is deterministic and explained point by point. It only rewards keywords that
actually appear in the resume, and the resume can only contain verified facts, so the way to raise
it is to add real experience to the profile, never to stuff keywords.
"""
from __future__ import annotations

import re

from backend.models.profile import Profile
from backend.models.resume import TailoredDraft
from backend.services.facts import verified_technologies
from backend.services.scoring import ALIASES, _mentions, canon

_ALIAS_PAIRS = list(ALIASES.items())
WEIGHTS = {"required": 50, "preferred": 15, "title": 10, "terms": 10, "format": 15}
STOP = set("""a an the and or of to in for on with by at from as is are be we you our your will this that it
their they them who what which when where how all any can may must should able ability experience
experienced years year work working team teams role job strong excellent good great new using use
including include such other within across into over more most well also etc plus including""".split())


# Competencies ATS keyword matching doesn't reliably score; listed for interviews, not counted.
SOFT = re.compile(r"communicat|collaborat|product sense|leadership|problem[- ]solving|teamwork|ownership|passion|"
                  r"self[- ]starter|attention to detail|stakeholder|interpersonal|mentor|curiosity|adaptab|"
                  r"time management|critical thinking|work ethic|growth mindset|customer obsess", re.I)


# Filler in requirement phrases: "backend development" is about "backend".
FILLER = set("development developing engineering experience skills skill knowledge proficiency proficient "
             "understanding familiarity background expertise hands-on solid deep professional".split())


def _words(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#.\-]+", phrase.lower()) if w not in STOP | FILLER and len(w) > 2]


def _stem(w: str) -> str:
    return re.sub(r"(ing|ment|ments|ed|es|s)$", "", w)[:7] or w


def _phrase_in(phrase: str, text: str) -> bool:
    """Phrase-style requirements ('backend development') match when each meaningful word appears."""
    words = _words(phrase)
    stems = {_stem(w) for w in re.findall(r"[a-z0-9+#.]+", text)}
    return len(_words_all(phrase)) >= 2 and bool(words) and all(_stem(w) in stems for w in words)


def _words_all(phrase: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.]+", phrase.lower())


def _role_title(title: str) -> str:
    """'Full Stack Software Engineer, Credit Cards & Banking' -> 'Full Stack Software Engineer'."""
    return re.split(r",| - | – | \(|\|", title)[0]


def _text(draft: TailoredDraft) -> str:
    parts = [draft.summary.text] if draft.summary else []
    parts += [b.text for e in draft.experiences + draft.projects for b in e.bullets]
    parts += draft.skills
    return " ".join(parts).lower()


def _has(skill: str, text: str) -> bool:
    c = canon(skill)
    return _mentions(c, text) or _mentions(skill.lower(), text) or any(
        _mentions(alias, text) for alias, target in _ALIAS_PAIRS if target == c)


def _job_terms(description: str, limit: int = 15) -> list[str]:
    """Most repeated meaningful words and two-word phrases in the posting (what an ATS weighs).
    Phrases never span punctuation, so 'Credit Card & Banking' doesn't yield 'card banking'."""
    counts: dict[str, int] = {}
    for segment in re.split(r"[,.;:&()/\n•|!?]+|\s-\s|\band\b", description.lower()):
        words = [w for w in re.findall(r"[a-z][a-z0-9+#.\-]{2,}", segment) if w not in STOP]
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        for a, b in zip(words, words[1:]):
            counts[f"{a} {b}"] = counts.get(f"{a} {b}", 0) + 2  # phrases count more
    ranked = sorted((t for t, n in counts.items() if n >= 3), key=lambda t: -counts[t])
    out: list[str] = []
    for t in ranked:
        if not any(t in o or o in t for o in out):
            out.append(t)
        if len(out) == limit:
            break
    return out


def ats_score(draft: TailoredDraft, profile: Profile, *, title: str, description: str,
              required: list[str], preferred: list[str]) -> dict:
    text = _text(draft)
    verified = verified_technologies(profile) | {canon(s) for s in profile.skills}
    profile_text = " ".join([*profile.skills, *(b for e in profile.experience for b in [e.title, *e.bullets]),
                             *(t for p in profile.projects for t in [p.name, p.description, *p.outcomes])]).lower()
    soft = [s for s in dict.fromkeys(required + preferred) if SOFT.search(s)]
    required = [s for s in required if s not in soft]
    preferred = [s for s in preferred if s not in soft]

    def in_profile(s: str) -> bool:
        return canon(s) in verified or _has(s, profile_text) or _phrase_in(s, profile_text)

    def coverage(skills: list[str]) -> dict:
        found = [s for s in skills if _has(s, text) or _phrase_in(s, text)]
        missing = [s for s in skills if s not in found]
        return {"found": found, "missing_you_have": [s for s in missing if in_profile(s)],
                "missing_not_in_profile": [s for s in missing if not in_profile(s)],
                "ratio": len(found) / len(skills) if skills else 1.0}

    req, pref = coverage(required), coverage(preferred)

    title_words = [w for w in re.findall(r"[a-z0-9+#]+", _role_title(title).lower()) if w not in STOP and len(w) > 1]
    title_hits = [w for w in title_words if re.search(rf"\b{re.escape(w)}\b", text)]
    title_ratio = len(title_hits) / len(title_words) if title_words else 1.0

    terms = _job_terms(description)
    term_hits = [t for t in terms if t in text]
    terms_ratio = len(term_hits) / len(terms) if terms else 1.0

    words = len(text.split())
    fmt_checks = {
        "Standard section headings (Experience, Education, Skills)": bool(draft.experiences) and bool(draft.skills),
        "Contact details present": bool(profile.email and profile.name),
        "Single-column DOCX with no tables, images, or text boxes": True,  # how ApplyPilot renders
        "Dates in a consistent Mon YYYY format": True,  # rendered from the profile, always consistent
        "Length suits one or two pages (250–900 words)": 250 <= words <= 900,
    }
    fmt_ratio = sum(fmt_checks.values()) / len(fmt_checks)

    parts = {"required": req["ratio"], "preferred": pref["ratio"], "title": title_ratio, "terms": terms_ratio,
             "format": fmt_ratio}
    points = {k: round(WEIGHTS[k] * v, 1) for k, v in parts.items()}
    return {
        "score": round(sum(points.values())),
        "points": points, "weights": WEIGHTS,
        "required": req, "preferred": pref,
        "title": {"words": title_words, "found": title_hits},
        "job_terms": {"terms": terms, "found": term_hits},
        "format": fmt_checks, "word_count": words,
        "soft_skills": soft,
        "ceiling": round(WEIGHTS["required"] * (sum(map(in_profile, required)) / len(required) if required else 1.0)
                         + WEIGHTS["preferred"] * (sum(map(in_profile, preferred)) / len(preferred) if preferred else 1.0)),
    }
