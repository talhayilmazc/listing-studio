"use client";

import { useEffect, useState } from "react";
import { DEFAULT_MAX_CHARACTERS, DEFAULT_QUESTION, MAX_CHARACTERS, MAX_INSTRUCTIONS, MAX_QUESTION, personalizationErrors } from "@/lib/personalization";
import type { Personalization } from "@/lib/types";
import { Txt } from "./Txt";

const SOURCE: Record<string, string> = {
  listing: "this listing's own",
  profile: "from its profile",
  unknown: "from its profile, once its reference is read",
};

/**
 * A listing's personalization (v8 §D), under its description on the review card:
 * on or off, required, a character limit and instructions. It starts as the
 * profile's and can be changed for this listing; it goes with the listing to
 * every shop, and the draft is checked against it after it is made.
 */
export function ListingPersonalization({
  value,
  source,
  disabled,
  onSave,
  onUseForAll,
}: {
  value: Personalization | null;
  source: string;
  disabled?: boolean;
  /** null: follow the profile again. */
  onSave: (setting: Personalization | null) => Promise<void>;
  /** On a listing's card: give every listing of the batch this listing's setting. */
  onUseForAll?: (setting: Personalization) => Promise<void>;
}) {
  const blank: Personalization = { enabled: false, question_text: "", instructions: "", required: false, max_allowed_characters: null };
  const [draft, setDraft] = useState<Personalization>(value ?? blank);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);
  const incoming = JSON.stringify(value);
  useEffect(() => {
    // What is saved changed (this card saved, or "for all" did): show that. An
    // edit left unsaved here used to stay on screen as if it were the setting.
    setDraft(value ?? blank);
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming]);

  const set = (patch: Partial<Personalization>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
    setApplied(false);
  };

  async function useForAll() {
    setBusy(true);
    setError(null);
    try {
      // Exactly what this card shows: an edit not saved yet is saved with it.
      await onUseForAll?.(draft);
      setDirty(false);
      setApplied(true);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }
  const problems = personalizationErrors(draft);

  async function save(setting: Personalization | null) {
    setBusy(true);
    setError(null);
    try {
      await onSave(setting);
      setDirty(false);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset className="rounded-md border border-slate-200 px-3 py-2 text-xs" disabled={disabled || busy}>
      <legend className="px-1 text-xs font-medium text-slate-700">Personalization</legend>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex min-h-[2.75rem] items-center gap-2 sm:min-h-0">
          <input type="checkbox" className="h-4 w-4" checked={draft.enabled}
            onChange={(e) => set({ enabled: e.target.checked, question_text: draft.question_text || DEFAULT_QUESTION })} />
          <span>Buyers can personalize this listing</span>
        </label>
        <span className="text-slate-400">{SOURCE[source] ?? ""}</span>
      </div>
      {draft.enabled && (
        <div key="fields" className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className="space-y-0.5 sm:col-span-2">
            <span>{`Question (up to ${MAX_QUESTION} characters)`}</span>
            <input className="field py-1" maxLength={MAX_QUESTION} value={draft.question_text ?? ""}
              onChange={(e) => set({ question_text: e.target.value })} />
          </label>
          <label className="flex min-h-[2.75rem] items-center gap-2 sm:min-h-0">
            <input type="checkbox" className="h-4 w-4" checked={draft.required} onChange={(e) => set({ required: e.target.checked })} />
            <span>Required</span>
          </label>
          <label className="space-y-0.5">
            <span>{`Max characters (1–${MAX_CHARACTERS})`}</span>
            <input type="number" min={1} max={MAX_CHARACTERS} className="field py-1" value={draft.max_allowed_characters ?? ""} placeholder={String(DEFAULT_MAX_CHARACTERS)}
              onChange={(e) => set({ max_allowed_characters: e.target.value ? Number(e.target.value) : null })} />
          </label>
          <label className="space-y-0.5 sm:col-span-2">
            <span>{`Instructions for the buyer (up to ${MAX_INSTRUCTIONS})`}</span>
            <textarea className="field py-1" rows={2} maxLength={MAX_INSTRUCTIONS} value={draft.instructions ?? ""}
              onChange={(e) => set({ instructions: e.target.value })} />
          </label>
        </div>
      )}
      {problems.length > 0 && (
        <ul key="problems" className="mt-1 text-rose-700">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {dirty && (
          <button key="save" type="button" className="btn-secondary px-2 py-1 text-xs" disabled={problems.length > 0}
            onClick={() => save(draft)}>
            Save personalization
          </button>
        )}
        {source === "listing" && (
          <button key="reset" type="button" className="tap text-slate-500 underline" onClick={() => save(null)}>
            Use the profile&apos;s
          </button>
        )}
        {onUseForAll && (dirty || source === "listing") && (
          <button key="for-all" type="button" className="tap text-brand-700 underline" disabled={problems.length > 0}
            onClick={useForAll} data-testid="personalization-for-all">
            Use this for all listings
          </button>
        )}
        {applied && <span key="applied" role="status" className="text-emerald-700">Set on every listing of this batch</span>}
        {error && <span key="error" className="text-rose-700"><Txt>{error}</Txt></span>}
      </div>
    </fieldset>
  );
}
