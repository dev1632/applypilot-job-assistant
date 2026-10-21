import type {
  ApplicationDetail, Health, ImportSelection, Job, JobDetail, Profile, ResumeImportRecord, ResumeVersion, ScanReport,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;  // let the browser set the multipart boundary
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: isForm ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  stats: () => request<Record<string, number>>("/stats"),
  jobs: (params: { status?: string; freshness?: string } = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return request<Job[]>(`/jobs${q.size ? `?${q}` : ""}`);
  },
  job: (id: number) => request<JobDetail>(`/jobs/${id}`),
  transition: (appId: number, status: string, reason: string) =>
    post(`/applications/${appId}/transition`, { status, reason }),
  tailor: (jobId: number) => post<ResumeVersion>(`/jobs/${jobId}/resume`),
  tailorFromDescription: (body: { title: string; company: string; description: string; url?: string; location?: string }) =>
    post<{ job_id: number; resume: ResumeVersion }>("/resumes/from-description", body),
  resumesForJob: (jobId: number) => request<ResumeVersion[]>(`/jobs/${jobId}/resumes`),
  resume: (id: number) => request<ResumeVersion>(`/resumes/${id}`),
  reviewResume: (id: number, approve: boolean, note: string) =>
    post<ResumeVersion>(`/resumes/${id}/review`, { approve, note }),
  resumeDownloadUrl: (id: number) => `/api/resumes/${id}/download`,
  profile: () => request<Profile>("/profile"),
  saveProfile: (p: unknown) => request<Profile>("/profile", { method: "PUT", body: JSON.stringify(p) }),
  scan: () => post<ScanReport>("/discovery/scan"),
  importResume: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<ResumeImportRecord>("/profile/imports", { method: "POST", body });
  },
  applyImport: (id: string, pick: ImportSelection) =>
    post<{ profile: Profile; changes: string[] }>(`/profile/imports/${id}/apply`, pick),
  prepare: (jobId: number, applicationUrl?: string) =>
    post<ApplicationDetail>(`/jobs/${jobId}/prepare`, { application_url: applicationUrl || null }),
  application: (appId: number) => request<ApplicationDetail>(`/applications/${appId}`),
  answer: (appId: number, questionKey: string, answer: string | null, save = false) =>
    post<ApplicationDetail>(`/applications/${appId}/answers`, { question_key: questionKey, answer, confirm: true, save }),
  ready: (appId: number) => post<ApplicationDetail>(`/applications/${appId}/ready`),
  approve: (appId: number, jobId: number, resumeHash: string, formHash: string) =>
    post(`/applications/${appId}/approve`, { job_id: jobId, resume_hash: resumeHash, form_hash: formHash }),
  screenshotUrl: (appId: number, run: number) => `/api/applications/${appId}/screenshot?run=${run}`,
};
