"use client";

import { useId, useState } from "react";

/**
 * The longer explanation behind a "?" (B9): every screen says what it does in one sentence;
 * the rest is one tap away. The text toggles in place (no hover-only help: phones have none).
 */
export function Help({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="inline">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={id}
        aria-label={`More about ${label}`} data-testid="help"
        className="tap ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-300 align-middle text-[11px] font-semibold text-slate-600 hover:border-slate-500 hover:text-slate-900">
        ?
      </button>
      {open && (
        <span key="text" id={id} role="note" className="mt-1 block max-w-2xl rounded-md bg-slate-50 p-2 text-xs font-normal text-slate-700">
          {children}
        </span>
      )}
    </span>
  );
}
