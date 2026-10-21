export interface Score {
  skills_score: number;
  eligibility_status: "PASS" | "REVIEW" | "FAIL";
  match_reasons: string[];
  gaps: string[];
  eligibility_notes: string[];
  scored_at: string;
}

export interface Job {
  id: number;
  source: string;
  company: string;
  title: string;
  location: string | null;
  employment_type: string | null;
  url: string;
  requisition_id: string | null;
  posted_at: string | null;
  discovered_at: string;
  freshness_confidence: "VERIFIED" | "ESTIMATED" | "UNVERIFIED";
  possible_duplicate_of: number | null;
  status: string | null;
  latest_score: Score | null;
}

export interface Application {
  id: number;
  job_id: number;
  status: string;
  previous_status: string | null;
  resume_version_id: number | null;
  form_snapshot_hash: string | null;
  submitted_at: string | null;
  confirmation_id: string | null;
  last_error: string | null;
  follow_up_at: string | null;
}

export interface AppEvent {
  id: number;
  old_status: string | null;
  new_status: string;
  timestamp: string;
  details: string | null;
  evidence_path: string | null;
}

export interface JobDetail extends Job {
  description: string | null;
  application: Application | null;
  events: AppEvent[];
  allowed_next: string[];
}

export interface DraftBullet {
  text: string;
  source_fact_ids: string[];
}

export interface DraftEntry {
  entry_id: string;
  bullets: DraftBullet[];
}

export interface ResumeContent {
  summary: DraftBullet | null;
  experiences: DraftEntry[];
  projects: DraftEntry[];
  skills: string[];
}

export interface ResumeReport {
  method: "openai" | "rules";
  model: string | null;
  llm_attempts: number;
  change_summary: string[];
  matched_requirements: { requirement: string; fact_ids: string[] }[];
  gaps: string[];
  claims_requiring_review: string[];
  auto_corrections: string[];
  initial_violations: string[];
  cited_facts?: Record<string, string>;
  ats?: AtsScore;
  ats_changes?: string[];
  quality: {
    keyword_coverage: { covered: string[]; missing: string[]; ratio: number | null };
    chronology: string[];
    formatting: string[];
  };
}

export interface ResumeVersion {
  id: number;
  job_id: number;
  version: number;
  content_hash: string;
  created_at: string;
  review_status: "PENDING" | "APPROVED" | "REJECTED";
  approved_at: string | null;
  content: ResumeContent | null;
  report: ResumeReport | null;
}

export interface Experience {
  id: string;
  title: string;
  organization: string;
  start: string;
  end: string | null;
  bullets: string[];
  technologies: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  technologies: string[];
  outcomes: string[];
}

export interface Profile {
  name: string;
  email: string;
  phone?: string | null;
  approved_answers?: Record<string, string>;
  experience: Experience[];
  projects: Project[];
  [key: string]: unknown;
}

export interface Health {
  ok: boolean;
  llm_enabled: boolean;
  model: string;
  data_dir: string;
}

export interface ScanReport {
  fetched: number;
  skipped_role: number;
  duplicates: number;
  new_jobs: number;
  shortlisted: number;
  excluded: number;
  review: number;
  errors: string[];
}

export interface Answer {
  question_key: string;
  question_text: string;
  answer: string | null;
  verification_status: "VERIFIED" | "DRAFT" | "NEEDS_USER" | "USER_CONFIRMED" | "SKIPPED";
  category: string;
  source: string | null;
  field_type: string;
  required: boolean;
  sensitive: boolean;
  options: string[];
  note: string | null;
  reuse: { key: string | null; description: string };
}

export interface JobCheck {
  check: string;
  expected: string;
  ok: boolean;
  advisory?: boolean;
}

export interface Prep {
  mode: string;
  run: number;
  prepared_at: string;
  url: string;
  final_url: string | null;
  http_status: number | null;
  page_title: string;
  job_checks: JobCheck[];
  pause_reasons: string[];
  blocked_requests: string[];
  blocked_submits: number;
  screenshot: string | null;
  fields_found: number;
}

export interface ApprovalRecord {
  id: number;
  approved_at: string;
  consumed_at: string | null;
  approval_status: "ACTIVE" | "CONSUMED" | "INVALIDATED";
  resume_hash: string;
  form_hash: string;
}

export interface ApplicationDetail {
  application: Application;
  job: { id: number; company: string; title: string; location: string | null; url: string; requisition_id: string | null };
  resume: { id: number; version: number; content_hash: string; review_status: string } | null;
  application_url: string | null;
  prep: Prep | null;
  answers: Answer[];
  outstanding: { needs_answer: string[]; drafts_to_confirm: string[] };
  approvals: ApprovalRecord[];
  submission_mode: string;
}

export interface CheckedText {
  text: string;
  verbatim: boolean;
  flags: string[];
}

export interface FoundValue {
  value: string;
  found: boolean;
}

export interface ResumeImportRecord {
  id: string;
  created_at: string;
  file: string;
  method: "openai" | "rules";
  warnings: string[];
  contact: { field: string; value: string; found: boolean }[];
  skills: FoundValue[];
  education: { id: string; school: string; degree: string; field: string; graduation: string | null; coursework: string[]; flags: string[] }[];
  experience: {
    id: string; title: string; organization: string; start: string | null; end: string | null; current: boolean;
    technologies: FoundValue[]; bullets: CheckedText[]; flags: string[];
  }[];
  projects: { id: string; name: string; description: CheckedText; technologies: FoundValue[]; outcomes: CheckedText[]; flags: string[] }[];
}

export interface ImportSelection {
  contact: string[];
  skills: string[];
  education: string[];
  experience: { id: string; start: string; end: string | null; bullets: number[] }[];
  projects: { id: string; outcomes: number[] }[];
}

export interface AtsCoverage {
  found: string[];
  missing_you_have: string[];
  missing_not_in_profile: string[];
  ratio: number;
}

export interface AtsScore {
  score: number;
  ceiling: number;
  points: Record<"required" | "preferred" | "title" | "terms" | "format", number>;
  weights: Record<"required" | "preferred" | "title" | "terms" | "format", number>;
  required: AtsCoverage;
  preferred: AtsCoverage;
  title: { words: string[]; found: string[] };
  job_terms: { terms: string[]; found: string[] };
  format: Record<string, boolean>;
  word_count: number;
  soft_skills?: string[];
}
