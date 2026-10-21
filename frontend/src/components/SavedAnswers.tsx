import { useEffect, useState } from "react";
import type { Profile } from "../api/types";

/** Answers reused across applications. Work authorization and legal questions are deliberately not here. */
const COMMON: { key: string; label: string; hint: string; placeholder: string }[] = [
  { key: "country", label: "Country of residence", hint: "Used for country questions and phone-country pickers", placeholder: "United States" },
  { key: "location", label: "City", hint: "Entered into city type-ahead fields at submission", placeholder: "Syracuse, NY" },
  { key: "referral", label: "How did you hear about us?", hint: "", placeholder: "Company website" },
  { key: "start_date", label: "Earliest start date", hint: "", placeholder: "January 2027" },
];
const KNOWN = new Set([...COMMON.map((c) => c.key), "prior_employment"]);

export function SavedAnswers({ profile, onSave, busy }: { profile: Profile; onSave: (p: Profile) => void; busy: boolean }) {
  const [phone, setPhone] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});

  useEffect(() => {
    setPhone(profile.phone ?? "");
    setAnswers({ ...(profile.approved_answers ?? {}) });
  }, [profile]);

  const set = (key: string, value: string) => setAnswers((a) => ({ ...a, [key]: value }));
  const remove = (key: string) => setAnswers((a) => Object.fromEntries(Object.entries(a).filter(([k]) => k !== key)));
  const others = Object.entries(answers).filter(([k]) => !KNOWN.has(k));

  const save = () => {
    const cleaned = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, v.trim()]));
    onSave({ ...profile, phone: phone.trim() || null, approved_answers: cleaned });
  };

  return (
    <section className="card">
      <h2>Saved answers</h2>
      <p className="small muted">
        Reused on every application's dry run, and shown for review before you approve. Work-authorization questions use
        the verified facts in your profile instead, and legal statements are always asked fresh.
      </p>
      <div className="saved-grid">
        <label htmlFor="sa-phone">Phone</label>
        <div><input id="sa-phone" value={phone} placeholder="+1 315 555 0100" onChange={(e) => setPhone(e.target.value)} /></div>
        {COMMON.map((c) => (
          <FragmentRow key={c.key} id={`sa-${c.key}`} label={c.label} hint={c.hint}>
            <input id={`sa-${c.key}`} value={answers[c.key] ?? ""} placeholder={c.placeholder} onChange={(e) => set(c.key, e.target.value)} />
          </FragmentRow>
        ))}
        <FragmentRow id="sa-prior" label="Worked here before?"
          hint="If the company is in your verified experience, ApplyPilot answers Yes. Otherwise it uses this.">
          <select id="sa-prior" value={answers.prior_employment ?? ""} onChange={(e) => (e.target.value ? set("prior_employment", e.target.value) : remove("prior_employment"))}>
            <option value="">Ask me each time</option>
            <option value="No">No, for companies not in my experience</option>
          </select>
        </FragmentRow>
      </div>

      {others.length > 0 && (
        <>
          <h3>Saved for specific questions</h3>
          <ul className="saved-list">
            {others.map(([k, v]) => (
              <li key={k}>
                <span className="small"><code>{k}</code></span>
                <input aria-label={`Answer for ${k}`} value={v} onChange={(e) => set(k, e.target.value)} />
                <button className="link danger" onClick={() => remove(k)} aria-label={`Remove saved answer ${k}`}>Remove</button>
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="row">
        <button className="primary" onClick={save} disabled={busy}>Save answers</button>
      </div>
    </section>
  );
}

function FragmentRow({ id, label, hint, children }: { id: string; label: string; hint: string; children: React.ReactNode }) {
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <div>
        {children}
        {hint && <p className="small muted hint">{hint}</p>}
      </div>
    </>
  );
}
