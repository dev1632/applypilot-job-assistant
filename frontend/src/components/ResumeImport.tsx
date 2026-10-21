import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { CheckedText, FoundValue, ImportSelection, Profile, ResumeImportRecord } from "../api/types";

const DATE = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Upload a resume, review the extracted draft, and add only what you pick to the profile. */
export function ResumeImport({ profile, onApplied }: { profile: Profile; onApplied: (p: Profile) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [record, setRecord] = useState<ResumeImportRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changes, setChanges] = useState<string[] | null>(null);
  const [pick, setPick] = useState<ImportSelection | null>(null);

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setChanges(null);
    try {
      const r = await api.importResume(file);
      setRecord(r);
      setPick(defaults(r, profile));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!record || !pick) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.applyImport(record.id, pick);
      setChanges(out.changes);
      setRecord(null);
      onApplied(out.profile);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>Import from your resume</h2>
      <p className="small muted">
        Upload a .docx, .pdf, or .txt resume. ApplyPilot copies what it says into a draft, checks every line against your
        file, and adds only what you tick. Your file stays in your local data folder.
      </p>
      <div className="row">
        <input type="file" accept=".docx,.pdf,.txt" aria-label="Resume file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="primary" disabled={!file || busy} onClick={upload}>{busy && !record ? "Reading…" : "Upload and read"}</button>
      </div>
      {error && <div className="alert tone-bad spaced">{error}</div>}
      {changes && changes.some((c) => c.includes("different")) && (
        <div className="alert tone-warn spaced">
          Some entries were already in your profile with different content and were left unchanged. Update them in the
          profile editor below if the resume version is right.
        </div>
      )}
      {changes && (
        <div className="alert tone-good spaced">
          <strong>Added to your profile.</strong>
          <ul className="plain small">{changes.length ? changes.map((c, i) => <li key={i}>{c}</li>) : <li>Nothing new was selected.</li>}</ul>
        </div>
      )}
      {record && pick && <Review record={record} profile={profile} pick={pick} setPick={setPick} />}
      {record && pick && (
        <div className="row spaced">
          <button className="primary" disabled={busy || !valid(pick)} onClick={apply}>Add selected to profile</button>
          <button onClick={() => setRecord(null)} disabled={busy}>Discard draft</button>
          {!valid(pick) && <span className="small tone-text-warn">Every selected job needs start (and end) dates as YYYY-MM.</span>}
        </div>
      )}
    </section>
  );
}

function defaults(r: ResumeImportRecord, profile: Profile): ImportSelection {
  const current: Record<string, unknown> = { name: profile.name, email: profile.email, phone: profile.phone };
  const links = (profile as { links?: Record<string, string> }).links ?? {};
  const haveSkills = new Set(((profile as { skills?: string[] }).skills ?? []).map((s) => s.toLowerCase()));
  return {
    // Fill empty fields by default; never overwrite something you already have unless you tick it.
    contact: r.contact.filter((c) => c.found && !(c.field.startsWith("links.") ? links[c.field.slice(6)] : current[c.field])).map((c) => c.field),
    skills: r.skills.filter((s) => s.found && !haveSkills.has(s.value.toLowerCase())).map((s) => s.value),
    education: r.education.filter((e) => !e.flags.length).map((e) => e.id),
    experience: r.experience
      .filter((e) => !e.flags.some((f) => !/date|month/i.test(f)))
      .map((e) => ({ id: e.id, start: e.start ?? "", end: e.current ? null : e.end, bullets: e.bullets.flatMap((b, i) => (b.verbatim ? [i] : [])) })),
    projects: r.projects.filter((p) => !p.flags.length).map((p) => ({ id: p.id, outcomes: p.outcomes.flatMap((o, i) => (o.verbatim ? [i] : [])) })),
  };
}

function valid(p: ImportSelection) {
  return p.experience.every((e) => DATE.test(e.start) && (!e.end || (DATE.test(e.end) && e.end >= e.start)));
}

function Review({ record, profile, pick, setPick }: {
  record: ResumeImportRecord; profile: Profile; pick: ImportSelection; setPick: (p: ImportSelection) => void;
}) {
  const toggle = <K extends "contact" | "skills" | "education">(key: K, v: string) =>
    setPick({ ...pick, [key]: pick[key].includes(v) ? pick[key].filter((x) => x !== v) : [...pick[key], v] });
  const expPick = useMemo(() => Object.fromEntries(pick.experience.map((e) => [e.id, e])), [pick.experience]);
  const projPick = useMemo(() => Object.fromEntries(pick.projects.map((p) => [p.id, p])), [pick.projects]);
  const currentContact: Record<string, string | null | undefined> = { name: profile.name, email: profile.email, phone: profile.phone };
  const links = (profile as { links?: Record<string, string> }).links ?? {};

  const setExp = (id: string, patch: Partial<ImportSelection["experience"][number]> | null) => {
    const e = record.experience.find((x) => x.id === id)!;
    const others = pick.experience.filter((x) => x.id !== id);
    if (patch === null) return setPick({ ...pick, experience: others });
    const base = expPick[id] ?? { id, start: e.start ?? "", end: e.current ? null : e.end, bullets: e.bullets.flatMap((b, i) => (b.verbatim ? [i] : [])) };
    setPick({ ...pick, experience: [...others, { ...base, ...patch }] });
  };
  const setProj = (id: string, outcomes: number[] | null) => {
    const others = pick.projects.filter((x) => x.id !== id);
    setPick({ ...pick, projects: outcomes === null ? others : [...others, { id, outcomes }] });
  };

  return (
    <div className="import-review spaced">
      <p className="small muted">
        From <code>{record.file}</code> · {record.method === "openai" ? "read with OpenAI, then checked line by line" : "basic reader (no OpenAI key)"}.
        Items marked <span className="badge tone-warn">check</span> weren't found word for word in your file and start unticked.
      </p>
      {record.warnings.map((w) => <div key={w} className="alert tone-warn">{w}</div>)}

      {record.contact.length > 0 && (
        <>
          <h3>Contact</h3>
          {record.contact.map((c) => {
            const cur = c.field.startsWith("links.") ? links[c.field.slice(6)] : currentContact[c.field];
            return (
              <label key={c.field} className="check import-line">
                <input type="checkbox" checked={pick.contact.includes(c.field)} onChange={() => toggle("contact", c.field)} />
                <span><strong>{c.field.replace("links.", "")}</strong>: {c.value}
                  {!c.found && <span className="badge tone-warn">check</span>}
                  {cur && cur !== c.value && <span className="small muted"> (replaces “{cur}”)</span>}
                </span>
              </label>
            );
          })}
        </>
      )}

      {record.skills.length > 0 && (
        <>
          <h3>Skills</h3>
          <div className="chips">
            {record.skills.map((s) => (
              <button key={s.value} type="button" aria-pressed={pick.skills.includes(s.value)}
                className={`chip toggle ${pick.skills.includes(s.value) ? "tone-good" : "tone-muted"}`} onClick={() => toggle("skills", s.value)}
                title={s.found ? undefined : "Not found in your resume"}>
                {pick.skills.includes(s.value) ? "✓ " : ""}{s.value}{!s.found && " ⚠"}
              </button>
            ))}
          </div>
        </>
      )}

      {record.experience.length > 0 && <h3>Experience</h3>}
      {record.experience.map((e) => {
        const sel = expPick[e.id];
        return (
          <div key={e.id} className={`import-entry ${sel ? "" : "off"}`}>
            <label className="check">
              <input type="checkbox" checked={!!sel} onChange={(ev) => setExp(e.id, ev.target.checked ? {} : null)} />
              <strong>{e.title}, {e.organization}</strong>
            </label>
            {e.flags.map((f) => <p key={f} className="small tone-text-warn">⚠ {f}</p>)}
            {sel && (
              <div className="row dates">
                <label className="small">Start <input aria-label={`Start month for ${e.title}`} placeholder="YYYY-MM" value={sel.start}
                  onChange={(ev) => setExp(e.id, { start: ev.target.value.trim() })} /></label>
                <label className="small">End <input aria-label={`End month for ${e.title}`} placeholder="YYYY-MM or blank if current" value={sel.end ?? ""}
                  onChange={(ev) => setExp(e.id, { end: ev.target.value.trim() || null })} /></label>
              </div>
            )}
            <Bullets items={e.bullets} picked={sel?.bullets ?? []} disabled={!sel}
              onChange={(b) => setExp(e.id, { bullets: b })} />
            <Techs items={e.technologies} />
          </div>
        );
      })}

      {record.projects.length > 0 && <h3>Projects</h3>}
      {record.projects.map((p) => {
        const sel = projPick[p.id];
        return (
          <div key={p.id} className={`import-entry ${sel ? "" : "off"}`}>
            <label className="check">
              <input type="checkbox" checked={!!sel} onChange={(ev) => setProj(p.id, ev.target.checked ? p.outcomes.flatMap((o, i) => (o.verbatim ? [i] : [])) : null)} />
              <strong>{p.name}</strong>
            </label>
            {p.flags.map((f) => <p key={f} className="small tone-text-warn">⚠ {f}</p>)}
            <p className={p.description.verbatim ? "" : "tone-text-warn"}>{p.description.text}{!p.description.verbatim && " ⚠ not found word for word"}</p>
            <Bullets items={p.outcomes} picked={sel?.outcomes ?? []} disabled={!sel} onChange={(o) => setProj(p.id, o)} />
            <Techs items={p.technologies} />
          </div>
        );
      })}

      {record.education.length > 0 && <h3>Education</h3>}
      {record.education.map((e) => (
        <label key={e.id} className="check import-line">
          <input type="checkbox" checked={pick.education.includes(e.id)} onChange={() => toggle("education", e.id)} />
          <span>{e.degree} in {e.field}, {e.school}{e.graduation ? ` · ${e.graduation}` : " · graduation date missing"}
            {e.flags.length > 0 && <span className="badge tone-warn">check</span>}</span>
        </label>
      ))}
    </div>
  );
}

function Bullets({ items, picked, disabled, onChange }: { items: CheckedText[]; picked: number[]; disabled: boolean; onChange: (v: number[]) => void }) {
  if (!items.length) return null;
  return (
    <ul className="import-bullets">
      {items.map((b, i) => (
        <li key={i} className={b.verbatim ? "" : "flagged"}>
          <label className="check">
            <input type="checkbox" disabled={disabled} checked={picked.includes(i)}
              onChange={() => onChange(picked.includes(i) ? picked.filter((x) => x !== i) : [...picked, i])} />
            <span>
              {b.text}
              {b.flags.map((f) => <span key={f} className="small tone-text-warn"><br />⚠ {f}</span>)}
            </span>
          </label>
        </li>
      ))}
    </ul>
  );
}

function Techs({ items }: { items: FoundValue[] }) {
  if (!items.length) return null;
  return (
    <p className="small muted">
      Technologies: {items.map((t, i) => (
        <span key={t.value}>{i > 0 && ", "}<span className={t.found ? "" : "tone-text-warn"} title={t.found ? undefined : "Not in your resume; won't be imported"}>
          {t.value}{!t.found && " (not in resume, skipped)"}</span></span>
      ))}
    </p>
  );
}
