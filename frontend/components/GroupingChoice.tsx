"use client";

import { useState } from "react";
import { MODES, MODE_LABELS, preview, previewText, type GroupingMode, type PreviewFile } from "@/lib/grouping";

/**
 * "How should these photos become listings?" One of three choices, each with what
 * it would make ("42 listings, 3 photos unsorted"), counted from the files with
 * the same rules the server uses (lib/grouping.ts). Shown before upload and on the
 * batch until anything is written, so the seller can switch first.
 */
export function GroupingChoice({
  files,
  value,
  onChange,
  disabled,
  extra,
  name = "grouping",
}: {
  files: PreviewFile[];
  value: GroupingMode;
  onChange: (mode: GroupingMode) => void;
  disabled?: boolean;
  /** Said under the choices, e.g. that ZIPs are counted once unpacked. */
  extra?: string;
  name?: string;
}) {
  // While a switch is being saved the control is disabled; show the pick, not the old value.
  const [pending, setPending] = useState<GroupingMode | null>(null);
  const shown = disabled && pending ? pending : value;
  return (
    <fieldset className="space-y-2" disabled={disabled}>
      <legend className="text-sm font-medium text-slate-800">How should these photos become listings?</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {MODES.map((mode) => {
          const counted = files.length ? previewText(preview(files, mode)) : null;
          const on = shown === mode;
          return (
            <label
              key={mode}
              className={
                "flex min-h-[44px] cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm " +
                (on ? "border-brand-600 bg-brand-50" : "border-slate-200 bg-white hover:border-slate-300")
              }
            >
              <input
                type="radio"
                name={name}
                className="mt-1 shrink-0"
                checked={on}
                onChange={() => {
                  setPending(mode);
                  onChange(mode);
                }}
                data-testid={`grouping-${mode}`}
              />
              <span className="min-w-0">
                <span className="block font-medium text-slate-900">{MODE_LABELS[mode].label}</span>
                <span className="block text-xs text-slate-500">{MODE_LABELS[mode].detail}</span>
                {counted && (
                  <span key="count" translate="no" className={"mt-1 block text-xs font-medium " + (on ? "text-brand-700" : "text-slate-600")}>
                    {counted}
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </div>
      {extra && (
        <p key="extra" className="text-xs text-slate-500">
          <span>{extra}</span>
        </p>
      )}
    </fieldset>
  );
}
