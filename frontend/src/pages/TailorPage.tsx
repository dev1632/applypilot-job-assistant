import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { ErrorNote } from "../components/Layout";

/** Paste a job description; get a tailored resume scored for ATS. */
export function TailorPage() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [url, setUrl] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tooShort = description.trim().length < 200;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api.tailorFromDescription({ title, company, description, url: url || undefined, location: location || undefined });
      navigate(`/resumes/${out.resume.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Paste a job</h1>
          <p className="subtitle">Get a resume tailored to one job description, with its ATS match score.</p>
        </div>
      </div>
      <ErrorNote error={error} />
      <form className="card stack tailor-form" onSubmit={submit}>
        <div className="grid-2 tight">
          <label>Job title<input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Software Engineer, Backend" /></label>
          <label>Company<input required value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Northwind" /></label>
          <label>Location <span className="muted small">(optional)</span><input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="New York, NY" /></label>
          <label>Posting URL <span className="muted small">(optional)</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></label>
        </div>
        <label>
          Job description
          <textarea required rows={16} value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="Paste the full posting: responsibilities, requirements, and nice-to-haves." />
        </label>
        <p className="small muted">
          The resume uses only facts from your profile. It uses the job's wording for skills you have and lists skills you
          don't have as gaps. It never adds them. The description is sent to OpenAI to pick out the requirements.
        </p>
        <div className="row">
          <button className="primary" disabled={busy || tooShort || !title.trim() || !company.trim()}>
            {busy ? "Tailoring… (about 20 seconds)" : "Generate tailored resume"}
          </button>
          {tooShort && description && <span className="small muted">Paste the full description (at least 200 characters).</span>}
        </div>
      </form>
    </>
  );
}
