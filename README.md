# ApplyPilot

Local-first, four-agent job application assistant. This is the backend for milestones **M1 (foundation)**
**M2 (discovery)**, **M3 (resume tailoring)**, plus the backend approval gate from M5. The design doc says to build these first.

| Agent | Module | Status |
|---|---|---|
| 1. Job Discovery | `backend/agents/job_discovery.py` | ✅ Greenhouse + Lever adapters, normalization, dedup, freshness, scoring, eligibility |
| 4. Application Tracker | `backend/agents/application_tracker.py` | ✅ State machine, append-only events, follow-ups |
| Dashboard | `frontend/` | ✅ Jobs, job detail, resume review, profile & scan |
| 2. Resume Tailoring | `backend/agents/resume_tailor.py` | ✅ OpenAI drafting, fabrication verifier, versioned DOCX |
| 3. Application Assistant | `backend/agents/application_assistant.py` | ✅ Dry run only: sandboxed fill, answer review, approval |

The approval gate (`backend/safety/approval.py`) is already enforced on the backend: approval is bound to
job id + resume hash + form hash, can be used only once, is invalidated by any content change, and
`SUBMITTED` requires evidence.

## LLM (OpenAI)

`backend/services/llm.py` uses the OpenAI Responses API with structured outputs (Pydantic schemas).
Job postings are passed as delimited untrusted data. Every extracted claim (sponsorship, citizenship,
graduation window) needs a verbatim quote, which is checked against the posting before it's used.
If no key is configured, the system falls back to rule-based analysis.

## Resume tailoring (Agent 2)

Your profile is the master record and the only source of facts. Each experience bullet, project
description, and outcome becomes a fact with an id (`exp:acme:b0`, `proj:bot:out0`, ...).

1. OpenAI drafts the resume. Every bullet must cite the fact ids it is based on.
2. `backend/safety/resume_verifier.py` checks the draft. These are hard failures:
   - a number (metric, %, count) that isn't in the cited facts
   - a technology the cited facts don't support
   - a citation to an unknown fact or to a different job/project
   - an unverified skill
3. On failure the model gets one repair attempt with the exact problems listed. Anything still failing
   is replaced with your original wording or dropped. An unverified claim is never shipped.
4. Softer issues (wording like "led" or "owned" that your facts don't support, or heavy rewording) are
   listed under `claims_requiring_review`, and the job goes to `REVIEW_REQUIRED` until you approve.
5. Each run saves `data/resumes/job_<id>/resume_v<n>.docx` plus a `.json` audit file mapping every
   bullet to its source facts. The resume hash is what the approval gate binds to.

Without an OpenAI key, it selects and reorders your bullets by relevance, word for word.

Endpoints: `POST /jobs/{id}/resume`, `GET /jobs/{id}/resumes`, `GET /resumes/{id}`,
`GET /resumes/{id}/download`, `POST /resumes/{id}/review` (`{"approve": true}`).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set OPENAI_API_KEY, or store it in Keychain:
security add-generic-password -s applypilot -a OPENAI_API_KEY -w
.venv/bin/uvicorn backend.api.main:app --reload
```

With the demo data, the Northwind job points at a local practice form, so you can try the dry run.

Then `PUT /profile` with your verified facts (see `profile.example.json`) and `POST /discovery/scan`.
If you have an older `data/applypilot.db` from before Agent 2, delete it (there are no migrations yet).
API docs are at http://localhost:8000/docs. For a scheduled scan, run `python -m backend.cli scan`.

## Paste a job (tailored resume + ATS score)

**Paste a job** in the dashboard (or `POST /resumes/from-description`): paste a title, company and
description, and get a tailored resume version with an ATS match score out of 100:

| Part | Points |
|---|---|
| Required skills found | 50 |
| Preferred skills found | 15 |
| Role-title words found | 10 |
| The posting's most repeated terms | 10 |
| Parseable format | 15 |

Soft skills ("communication", "product sense") are listed but not scored, since keyword matching can't
judge them.

To raise the score truthfully, ApplyPilot:
- uses the job's spelling for skills you have
- adds every required skill that's in your verified profile
- puts the job's skills first
- keeps every job you've held, newest first

Skills you don't have are shown as gaps along with the best score your profile can reach. They are
never added. The same posting pasted again becomes a new version of the same job. A pasted job gets a
resume even while it waits on an eligibility review.

## Application assistant (Agent 3), dry run only

`POST /jobs/{id}/prepare` opens the application page in headless Chromium (Playwright), checks that
the company, title and requisition ID match, then fills the form and reads every value back.

**It cannot submit.** `backend/services/browser.py` never clicks. Its init script cancels submit
events, disables `form.submit()` / `requestSubmit()` and swallows submit-button clicks. Every
non-GET request is aborted at the network layer, which also stops tricks such as borrowing a native
`submit` from an iframe. `POST /applications/{id}/submit` always returns 403.
Once the page has loaded, the browser goes fully offline before any field is touched, so typed values
can't leave it by any route (even GET beacons). Files are never attached during a dry run: Greenhouse,
for example, uploads a resume to its file storage the moment it's attached. The run records which
approved file will be used instead.

Tested on real Greenhouse forms (the newer hosted job board, the older one, and a company career page
that embeds the form in an iframe). It handles React-style dropdowns, invisible reCAPTCHA (noted,
not paused) and "Attach"-labeled uploads. Work-authorization answers are only given for questions
about the U.S. Type-ahead fields such as City are left for you.

**How each field is answered** (`backend/safety/field_classifier.py`):
- Contact details, links and education come from your profile.
- Work authorization, sponsorship and citizenship come only from verified profile facts; if one is
  missing, the field waits for you.
- Demographic (EEO) questions use only answers you pre-approved in `approved_answers`.
- Legal attestations, consents and signatures always wait for you.
- Open-ended questions get an OpenAI draft from your facts. Invented numbers or tools are discarded,
  and personal reasons are left as `[ADD: …]` placeholders you fill in.
- Optional questions with no verified answer are left blank.

**Import from your resume** (Profile & scan): upload a .docx, .pdf or .txt (5 MB max). The file is
stored privately in `data/master_resume/` and only read for text. OpenAI copies its contents into a draft
(`data/imports/`), then code checks every bullet, number, skill, technology and contact detail against
the file's text. Anything not found word for word is flagged and starts unticked. You pick what to
add, and fix job dates (YYYY-MM) if needed. Existing profile entries are never changed, and repeats are
skipped. Without an OpenAI key, only contact details and the Skills line are read. Scanned PDFs (images)
aren't supported.

**Saved answers** (Profile & scan → Saved answers, stored in `profile.phone` and `approved_answers`):
phone, country of residence, city, "how did you hear about us", start date, and a default for
"have you worked here before?". That default is only used for companies that aren't in your verified
experience: if the company is listed there, the answer is Yes. City type-ahead fields are recorded in
the dry run and entered at submission. On the review screen, any answer can be saved for next time with
"Save for future applications", except legal and work-authorization answers and files.

CAPTCHAs, blocked pages and job mismatches pause the run for review; they are never bypassed.
You confirm drafts and answer what's left, then "Send to final approval" and approve. The approval
is tied to the job, the resume hash and the hash of all answers, and any later edit cancels it.
Screenshots and snapshots go to `data/applications/` with owner-only permissions.

Setup: `.venv/bin/python -m playwright install chromium-headless-shell`.

## Dashboard (React + TypeScript + Vite)

```bash
cd frontend && npm install && npm run dev     # http://localhost:5173 (proxies /api to :8000)
```

- **Jobs**: status tabs with counts, posting-date confidence, fit score, and eligibility.
- **Job detail**: match reasons, gaps, eligibility notes, a "Generate tailored resume" button, manual
  status moves (with a required reason), and the full history.
- **Resume review**: the tailored resume with each bullet labeled "your original wording" or
  "reworded" (expand to see its source facts), flagged claims highlighted, blocked edits listed,
  keyword coverage and gaps, plus Approve / Reject / Download DOCX. If claims are flagged, you must
  confirm them before approving.
- **Application review**: run the dry-run fill, see a screenshot of the filled form, answer or
  confirm each question (sensitive ones are marked), then the final approval screen. In dry-run
  mode the Submit button stays disabled.
- **Profile & scan**: edit your verified profile (JSON) and run a discovery scan.

To try it with sample data (kept apart from your real data):

```bash
APPLYPILOT_DATA_DIR=./demo_data APPLYPILOT_USE_LLM=false .venv/bin/python -m backend.demo_seed
APPLYPILOT_DATA_DIR=./demo_data APPLYPILOT_DEV_ROUTES=true .venv/bin/uvicorn backend.api.main:app --port 8000
```

## Tests

```bash
.venv/bin/python -m pytest -q backend/tests
```

Personal data (`data/`, the DB, resumes, `.env`) is git-ignored.
