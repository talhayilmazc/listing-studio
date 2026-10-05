"use client";

import { useId, useState } from "react";
import { DEFAULT_REPLACE_MODE, replaceModes, type ReplaceMode } from "@/lib/replaceModes";

/**
 * The choice asked before "Replace images" touches a listing on Etsy: photos
 * only (the default) or photos with a new title and tags. Each option says what
 * it will change and what it leaves alone; nothing is sent until the seller
 * confirms.
 */
export function ReplaceChoice({
  intro,
  photos,
  confirmLabel,
  onConfirm,
  onCancel,
  pinActions = false,
}: {
  /** Which listing, in a sentence. */
  intro: string;
  /** Where the new photos come from and their order, e.g. "this group's, in the order shown". */
  photos: string;
  confirmLabel: (mode: ReplaceMode) => string;
  onConfirm: (mode: ReplaceMode) => void;
  onCancel: () => void;
  /** In a scrolling sheet: keep the buttons in view under the options. */
  pinActions?: boolean;
}) {
  const [mode, setMode] = useState<ReplaceMode>(DEFAULT_REPLACE_MODE);
  const name = useId();
  const options = replaceModes(photos);

  return (
    <div className="space-y-3 text-xs text-slate-700">
      <p className="text-slate-600">{intro}</p>
      <div role="radiogroup" aria-label="What to replace" className="space-y-2">
        {options.map((o) => {
          const on = mode === o.mode;
          return (
            <label
              key={o.mode}
              className={
                "block cursor-pointer rounded-md border px-3 py-2.5 " +
                (on ? "border-brand-600 bg-brand-50" : "border-slate-200 bg-white hover:border-slate-300")
              }
            >
              <span className="flex items-start gap-2.5">
                <input
                  type="radio"
                  name={name}
                  className="mt-0.5"
                  checked={on}
                  onChange={() => setMode(o.mode)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-slate-900">{o.label}</span>
                  <span className="mt-1.5 block font-medium text-slate-700">Changes</span>
                  <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-slate-600">
                    {o.changes.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ul>
                  <span className="mt-1.5 block font-medium text-slate-700">Stays as it is</span>
                  <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-slate-600">
                    {o.keeps.map((k) => (
                      <li key={k}>{k}</li>
                    ))}
                  </ul>
                  <span className="mt-1.5 block text-slate-500">{o.counts}</span>
                </span>
              </span>
            </label>
          );
        })}
      </div>
      <div className={"flex flex-wrap items-center gap-3 " + (pinActions ? "sticky bottom-0 -mx-4 border-t border-slate-100 bg-white px-4 py-3" : "")}>
        <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={() => onConfirm(mode)}>
          <span>{confirmLabel(mode)}</span>
        </button>
        <button type="button" className="tap text-slate-600 underline" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
