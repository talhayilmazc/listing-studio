"use client";

import { useState } from "react";
import type { Personalization, Profile } from "@/lib/types";

const SOURCE: Record<string, string> = {
  reference: "copied from your reference listing",
  custom: "your own setting",
  unknown: "read from the reference on its next refresh",
};

/**
 * Personalization on a profile (v7 §D4): what new drafts get, copied from the
 * reference listing unless the seller sets their own. Etsy takes one text
 * question (1-45 characters), with optional instructions and a character limit.
 */
export function PersonalizationEditor({
  profile,
  busy,
  onSave,
}: {
  profile: Profile;
  busy: boolean;
  onSave: (setting: Personalization | null) => Promise<void>;
}) {
  const current = profile.personalization;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Personalization>(
    current ?? { enabled: false, question_text: "", instructions: "", required: false, max_allowed_characters: null },
  );

  function open() {
    setDraft(
      current?.enabled
        ? { ...current }
        : { enabled: true, question_text: "", instructions: "", required: false, max_allowed_characters: null },
    );
    setEditing(true);
  }

  return (
    <div className="rounded-md border border-slate-200 px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-slate-700">Personalization</span>
        <span className="text-slate-400">{SOURCE[profile.personalization_source] ?? ""}</span>
      </div>
      {!editing && (
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-600">
          {current == null ? (
            <span>Not read yet.</span>
          ) : current.enabled ? (
            <span>
              &ldquo;{current.question_text}&rdquo;
              {current.required ? " · required" : " · optional"}
              {current.max_allowed_characters ? ` · up to ${current.max_allowed_characters} characters` : ""}
              {current.instructions ? ` · ${current.instructions}` : ""}
            </span>
          ) : (
            <span>Off: new drafts are not personalizable.</span>
          )}
          <button type="button" className="underline hover:text-slate-900" onClick={open} disabled={busy}>
            edit
          </button>
          {current?.enabled && (
            <button
              type="button"
              className="underline hover:text-slate-900"
              onClick={() => onSave({ enabled: false, question_text: null, instructions: null, required: false, max_allowed_characters: null })}
              disabled={busy}
            >
              turn off
            </button>
          )}
          {profile.personalization_source === "custom" && (
            <button type="button" className="underline hover:text-slate-900" onClick={() => onSave(null)} disabled={busy}>
              use the reference&apos;s
            </button>
          )}
        </div>
      )}
      {editing && (
        <form
          className="mt-2 space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            await onSave(draft);
            setEditing(false);
          }}
        >
          <label className="block text-slate-600">
            Question the buyer answers
            <input
              className="field mt-0.5 py-1 text-xs"
              maxLength={45}
              value={draft.question_text ?? ""}
              onChange={(e) => setDraft({ ...draft, question_text: e.target.value })}
              placeholder="e.g. Name to print"
              required
            />
          </label>
          <label className="block text-slate-600">
            Instructions (optional)
            <input
              className="field mt-0.5 py-1 text-xs"
              maxLength={256}
              value={draft.instructions ?? ""}
              onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
            />
          </label>
          <div className="flex flex-wrap items-center gap-4 text-slate-600">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={draft.required}
                onChange={(e) => setDraft({ ...draft, required: e.target.checked })}
              />
              Required
            </label>
            <label className="flex items-center gap-1.5">
              Character limit
              <input
                type="number"
                min={1}
                max={1024}
                className="field w-20 py-0.5 text-xs"
                value={draft.max_allowed_characters ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, max_allowed_characters: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
          </div>
          <div className="flex gap-2">
            <button type="submit" className="btn-primary px-2.5 py-1 text-xs" disabled={busy}>
              Save
            </button>
            <button type="button" className="text-slate-500 underline" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
