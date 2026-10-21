import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ScanReport } from "../api/types";
import { useAsync } from "../api/useAsync";
import { ErrorNote } from "../components/Layout";
import { ResumeImport } from "../components/ResumeImport";
import { SavedAnswers } from "../components/SavedAnswers";
import type { Profile } from "../api/types";

export function ProfilePage() {
  const profile = useAsync(() => api.profile(), []);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanReport | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (profile.data) setText(JSON.stringify(profile.data, null, 2));
  }, [profile.data]);

  const save = async () => {
    setError(null);
    setSaved(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setError(`Not valid JSON: ${(e as Error).message}`);
      return;
    }
    setBusy(true);
    try {
      const p = await api.saveProfile(parsed);
      setText(JSON.stringify(p, null, 2));
      setSaved("Profile saved.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const saveAnswers = async (p: Profile) => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      profile.setData(await api.saveProfile(p));
      setSaved("Saved answers updated.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runScan = async () => {
    setBusy(true);
    setError(null);
    setScan(null);
    try {
      setScan(await api.scan());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <h1>Profile &amp; scan</h1>
        <button className="primary" onClick={runScan} disabled={busy}>
          {busy ? "Working…" : "Run discovery scan"}
        </button>
      </div>
      <ErrorNote error={error ?? profile.error} />
      {saved && <div className="alert tone-good">{saved}</div>}
      {scan && (
        <div className="card">
          <h2>Scan result</h2>
          <div className="stats">
            {(["fetched", "new_jobs", "duplicates", "shortlisted", "review", "excluded", "skipped_role"] as const).map((k) => (
              <div key={k} className="stat">
                <span className="stat-num">{scan[k]}</span>
                <span className="stat-label">{k.replace("_", " ")}</span>
              </div>
            ))}
          </div>
          {scan.errors.length > 0 && <ul className="plain tone-text-bad">{scan.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
        </div>
      )}
      {profile.data && <ResumeImport profile={profile.data} onApplied={(p) => profile.setData(p)} />}
      {profile.data && <SavedAnswers profile={profile.data} onSave={saveAnswers} busy={busy} />}
      <section className="card">
        <h2>Verified career profile</h2>
        <p className="small muted">
          This is the only source of facts for your resumes. Only include things you can stand behind: every resume bullet
          must trace back to an entry here. Leave work-authorization fields as <code>null</code> if unsure, and jobs that
          depend on them will go to review rather than being guessed.
        </p>
        <textarea className="code" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} rows={28} />
        <div className="row">
          <button className="primary" onClick={save} disabled={busy}>Save profile</button>
          <button onClick={() => profile.data && setText(JSON.stringify(profile.data, null, 2))} disabled={busy}>
            Discard changes
          </button>
        </div>
      </section>
    </>
  );
}
