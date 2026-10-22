"use client";

import { useState } from "react";
import { NONE, NOT_SENT, PRODUCTION, TOOLS, cardLine, problem, toggleTool } from "@/lib/howMade";

/**
 * Etsy's two How it's made questions (lib/howMade.ts): one production answer, any tools or
 * None. Etsy's API cannot set them yet: the choice is kept and sent the day Etsy adds the field.
 */
export function HowMadeFields({
  production,
  tools,
  onChange,
  name,
}: {
  production: string | null;
  tools: string[];
  onChange: (production: string | null, tools: string[]) => void;
  /** Keeps the radio groups of two pickers on one page apart. */
  name: string;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <fieldset>
        <legend className="label">How does your shop produce this item?</legend>
        <div className="mt-1 space-y-1">
          {PRODUCTION.map((p) => (
            <label key={p.key} className="tap flex cursor-pointer items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name={`${name}-production`}
                checked={production === p.key}
                onChange={() => onChange(p.key, tools)}
              />
              <span>{p.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="label">What tools are used to make this item?</legend>
        <div className="mt-1 space-y-1">
          {TOOLS.map((t) => (
            <label key={t.key} className="tap flex cursor-pointer items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={tools.includes(t.key)}
                onChange={(e) => onChange(production, toggleTool(tools, t.key, e.target.checked))}
              />
              <span>{t.key === NONE ? "None of these" : t.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

/** The profile card's line, "How it's made: shop alters it · computerized tools · change". */
export function HowMadePicker({
  production,
  tools,
  onSave,
  id,
}: {
  production: string | null | undefined;
  tools: string[] | null | undefined;
  onSave: (production: string, tools: string[]) => Promise<void>;
  id: string;
}) {
  const [open, setOpen] = useState(false);
  const [p, setP] = useState<string | null>(production ?? null);
  const [t, setT] = useState<string[]>(tools ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = !!production && !!tools && tools.length > 0;
  const why = problem(p, t);

  async function save() {
    if (why || !p) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(p, t);
      setOpen(false);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="text-xs text-slate-700" data-testid="how-made">
      <p>
        <span>{cardLine(production, tools)}</span>
        <span> · </span>
        <button
          type="button"
          className="tap underline hover:text-slate-900"
          onClick={() => {
            setP(production ?? null);
            setT(tools ?? []);
            setOpen((o) => !o);
          }}
          data-testid="how-made-open"
        >
          {set ? "change" : "set it"}
        </button>
      </p>
      <p className="mt-0.5 text-[11px] leading-snug text-slate-600" data-testid="how-made-not-sent">{NOT_SENT}</p>
      {open && (
        <div key="form" className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-3">
          <HowMadeFields production={p} tools={t} onChange={(np, nt) => { setP(np); setT(nt); }} name={`how-made-${id}`} />
          {error && <p key="err" role="alert" className="mt-1 text-rose-700">{error}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary" disabled={!!why || saving} onClick={save} data-testid="how-made-save">
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
              Cancel
            </button>
            {why && <span key="why" className="text-slate-600">{why}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
