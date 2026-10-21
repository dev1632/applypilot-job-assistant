"""Decide how each application form field may be answered.

Policy (design doc sections 3 & 6):
  * Contact, education, and links come from the verified profile.
  * Work authorization / sponsorship / citizenship come only from user-verified profile facts;
    if a fact is missing the field waits for the user. Never inferred.
  * Voluntary demographic (EEO) questions use only answers the user pre-approved.
  * Optional questions without a verified answer are left blank; required ones wait for the user.
  * Legal attestations, consents, and signatures always wait for the user.
  * Narrative questions get a model-written DRAFT that the user must confirm.
  * Anything unrecognized waits for the user.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models.profile import Profile


@dataclass
class FormField:
    key: str  # stable id for this field on this form
    label: str
    field_type: str  # text | email | tel | url | textarea | select | combobox | radio | checkbox | file | custom
    required: bool = False
    options: list[str] = field(default_factory=list)
    name: str | None = None


@dataclass
class PlannedAnswer:
    value: str | None
    status: str  # VERIFIED | DRAFT | NEEDS_USER | SKIPPED
    category: str
    source: str | None = None
    sensitive: bool = False
    note: str | None = None


RULES: list[tuple[str, re.Pattern]] = [(cat, re.compile(rx, re.I)) for cat, rx in [
    # Order matters: the first match wins.
    ("legal", r"\b(i (hereby )?(certify|attest|acknowledge|agree|accept|consent|confirm|understand)|attestation|"
              r"\bconsent\b|terms|privacy (policy|notice)|aviso de privacidade"
              r"|signature|sign here|background (check|screening)|drug (test|screen)|non-?compete|arbitration)"),
    ("eeo", r"\b(gender|sex\b|race|ethnic|hispanic|latin[oax]|veteran|military status|protected veteran|disabilit|sexual orientation|transgender"
            r"|pronoun|lgbt)"),
    ("sponsorship", r"sponsor|visa|h-?1b"),
    ("work_auth", r"(legally )?(authori[sz]ed|eligible|permitted|right) to work|work (authori[sz]ation|permit)"),
    ("citizenship", r"citizen|security clearance|clearance|export control|\bitar\b"),
    ("prior_employment", r"\b(previously|ever|formerly)\b.{0,40}\b(work(ed)?|employed|consult(ed)?|intern(ed)?)\b"
                         r"|former employee|re-?hire"),
    ("country", r"\bcountry\b"),
    ("resume_file", r"\b(resume|résumé|cv|curriculum vitae)\b"),
    ("cover_letter", r"cover letter"),
    ("first_name", r"^(legal )?first( name)?\b|given name"),
    ("last_name", r"^(legal )?last( name)?\b|surname|family name"),
    ("preferred_name", r"preferred (first )?name|nickname"),
    ("full_name", r"^(full |legal )?name\b"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile|cell"),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("website", r"website|portfolio|personal (site|url)|blog"),
    ("school", r"\b(school|university|college|institution)\b"),
    ("degree", r"\bdegree\b"),
    ("discipline", r"\b(major|discipline|field of study)\b"),
    ("graduation", r"graduat"),
    ("salary", r"salary|compensation|pay expectation|desired pay"),
    ("start_date", r"start date|available to start|earliest start|notice period"),
    ("referral", r"how did you hear|\breferr"),
    ("office_preference", r"office location|preferred office|which office|work from the office"),
    ("location", r"\b(location|city|address|where are you based|zip|postal)\b"),
    ("narrative", r"\b(why|describe|tell us|explain|what (excites|interests|motivates)|share|example of)\b"),
]]

RESUME = dict(RULES)["resume_file"]

# Saved answers that apply to every question of this kind (keyed by category in approved_answers).
# Everything else is saved for the exact question only. These are never saved: they must come from
# verified profile facts or the user's own decision each time.
REUSE_BY_CATEGORY = {"country", "location", "prior_employment", "referral", "start_date", "salary", "preferred_name"}
NEVER_SAVE = {"legal", "work_auth", "sponsorship", "citizenship", "resume_file", "cover_letter", "unknown_file"}
PROFILE_FIELDS = {"phone": "phone", "linkedin": "links.linkedin", "github": "links.github", "website": "links.website"}
SENSITIVE = {"legal", "eeo", "sponsorship", "work_auth", "citizenship", "salary"}


def classify(f: FormField) -> str:
    label = " ".join(f.label.split())
    if f.field_type == "file":
        hint = f"{label} {f.name or ''} {f.key}".replace("_", " ")
        if re.search(r"cover letter", hint, re.I):
            return "cover_letter"
        return "resume_file" if RESUME.search(hint) else "unknown_file"
    for cat, rx in RULES:
        if rx.search(label):
            if cat == "narrative" and f.field_type not in ("textarea", "text"):
                continue
            return cat
    return "narrative" if f.field_type == "textarea" else "unknown"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:120]


def pick_option(f: FormField, wanted: str) -> str | None:
    """Map an intended answer to one of a select/radio field's options, or None if unclear."""
    if not f.options:
        return wanted
    w = wanted.strip().lower()
    exact = [o for o in f.options if o.strip().lower() == w]
    if exact:
        return exact[0]
    starts = [o for o in f.options if o.strip().lower().startswith(w)]
    return starts[0] if len(starts) == 1 else None


def _yes_no(value: bool | None) -> str | None:
    return None if value is None else ("Yes" if value else "No")


# Case-sensitive on purpose: "US"/"U.S." and state codes, not the word "us" or lowercase "in"/"me".
US_PLACE = re.compile(r"\b(United States|united states|USA|U\.S\.A?\.?|US)\b|,\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|"
                      r"IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
                      r"VT|VA|WA|WV|WI|WY|DC)\b")
OTHER_PLACE = re.compile(r"country where|current country|your country|canada|united kingdom|\buk\b|israel|brazil|"
                         r"germany|india|ireland|france|mexico|singapore|japan|australia|netherlands|spain|poland", re.I)


def about_us_jurisdiction(label: str, job_location: str | None) -> bool | None:
    """True if the question is about working in the U.S., False if about elsewhere, None if unclear."""
    if US_PLACE.search(label):
        return True
    loc_us = bool(job_location and US_PLACE.search(job_location))
    if OTHER_PLACE.search(label):
        return bool(re.search(r"country where the job", label, re.I) and loc_us)
    return True if loc_us else None


def _same_company(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company)\b|[^a-z0-9]", "", x.lower())  # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def plan_answer(f: FormField, profile: Profile, *, resume_filename: str | None,
                job_location: str | None = None, company: str | None = None) -> PlannedAnswer:
    cat = classify(f)
    sensitive = cat in SENSITIVE
    approved = profile.approved_answers
    # An answer saved for this exact question wins; category-wide answers only for reusable kinds.
    pre = approved.get(slug(f.label)) or (approved.get(cat) if cat in REUSE_BY_CATEGORY | {"eeo"} else None)

    def verified(value: str | None, source: str, note: str | None = None) -> PlannedAnswer:
        if value is None or value == "":
            return waiting(note or "Not in your verified profile")
        chosen = pick_option(f, value)
        if chosen is None:
            return waiting(f"Your answer '{value}' doesn't match any option: {', '.join(f.options)}")
        return PlannedAnswer(chosen, "VERIFIED", cat, source, sensitive)

    def waiting(note: str) -> PlannedAnswer:
        # Optional questions are left blank (an unchecked box for optional consents); required ones wait.
        if not f.required:
            return PlannedAnswer(None, "SKIPPED", cat, None, sensitive, f"Optional; left blank. {note}")
        return PlannedAnswer(None, "NEEDS_USER", cat, None, sensitive, note)

    if f.field_type == "custom":
        return waiting("Custom form control that ApplyPilot can't fill reliably; fill it yourself")
    if f.field_type == "combobox" and not f.options and cat not in ("legal", "eeo"):
        if pre and cat not in NEVER_SAVE:
            return PlannedAnswer(pre, "VERIFIED", cat, "approved_answer", sensitive,
                                 "Type-ahead field: your saved answer is entered at submission, because picking a "
                                 "suggestion needs a live search that the dry run blocks")
        return waiting("Type-ahead field; ApplyPilot doesn't search on your behalf, so fill it yourself")
    if cat == "legal":
        return waiting("Legal attestation or consent: only you can answer this")
    if cat == "eeo":
        return verified(pre, "approved_answer") if pre else waiting("Voluntary self-identification: only you can answer this")

    auth = profile.work_authorization
    if cat in ("work_auth", "sponsorship", "citizenship"):
        us = about_us_jurisdiction(f.label, job_location)
        if us is not True:
            where = "another country" if us is False else "an unclear country"
            return waiting(f"This asks about {where}; your verified work-authorization facts cover the U.S. only")
    edu = profile.education[0] if profile.education else None
    first, _, last = profile.name.partition(" ")
    if cat == "work_auth":
        return verified(_yes_no(auth.authorized_in_us), "profile.work_authorization.authorized_in_us")
    if cat == "sponsorship":
        future = re.search(r"future", f.label, re.I)
        needs = auth.requires_sponsorship_now
        if future:
            vals = (auth.requires_sponsorship_now, auth.requires_sponsorship_future)
            needs = True if True in vals else (None if None in vals else False)
        return verified(_yes_no(needs), "profile.work_authorization.requires_sponsorship")
    if cat == "citizenship":
        value = auth.has_security_clearance if re.search(r"clearance", f.label, re.I) else auth.us_citizen
        return verified(_yes_no(value), "profile.work_authorization")

    if cat == "prior_employment":
        slugged = approved.get(slug(f.label))
        if slugged:
            return verified(slugged, "approved_answer")
        if company and any(_same_company(company, e.organization) for e in profile.experience):
            return verified("Yes", "profile.experience", f"{company} is in your verified experience")
        default = approved.get("prior_employment")
        if default:
            if f.options and pick_option(f, default) is None and default.strip().lower().startswith("no"):
                # Long-form options ("I have never worked at X"): use the one clearly negative option.
                negative = [o for o in f.options if re.search(r"\b(never|not|no)\b", o, re.I)]
                if len(negative) == 1:
                    return PlannedAnswer(negative[0], "VERIFIED", cat, "approved_answer (default: No)", sensitive)
            return verified(default, "approved_answer (default for companies not in your experience)")
        return waiting("Whether you've worked here before; save a default on your profile to reuse it")

    simple = {
        "resume_file": (resume_filename, "approved_resume"),
        "first_name": (first or None, "profile.name"),
        "last_name": (last or None, "profile.name"),
        "full_name": (profile.name or None, "profile.name"),
        "email": (profile.email or None, "profile.email"),
        "phone": (profile.phone, "profile.phone"),
        "linkedin": (profile.links.get("linkedin"), "profile.links.linkedin"),
        "github": (profile.links.get("github"), "profile.links.github"),
        "website": (profile.links.get("website") or profile.links.get("portfolio"), "profile.links"),
        "school": (edu.school if edu else None, "profile.education"),
        "degree": (edu.degree if edu else None, "profile.education"),
        "discipline": (edu.field if edu else None, "profile.education"),
        "graduation": (edu.expected_graduation if edu else None, "profile.education"),
    }
    if pre and cat != "resume_file":
        return verified(pre, "approved_answer")
    if cat in simple:
        value, source = simple[cat]
        return verified(value, source)
    if cat == "narrative":
        return PlannedAnswer(None, "DRAFT", cat, None, False, "Needs a drafted answer")
    if cat == "country":
        return waiting("Country isn't in your profile; save it under Saved answers to reuse it")
    return waiting("ApplyPilot doesn't recognize this question")
