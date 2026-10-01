"use client";

import { useEffect, useRef, useState } from "react";
import { Sheet } from "./Sheet";

export interface ShopChoice {
  id: string;
  name: string;
}

/** A shop's name wherever a listing, draft, schedule or figure is shown. */
export function ShopBadge({ name, className = "" }: { name: string | null | undefined; className?: string }) {
  if (!name) return null;
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ${className}`}
      title={`Shop: ${name}`}
    >
      <svg aria-hidden width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-slate-500">
        <path d="M3 9l1.5-5h15L21 9M4 9v11h16V9M3 9h18M9 20v-6h6v6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="truncate" translate="no">{name}</span>
    </span>
  );
}

/**
 * Choosing the shop something is for. Always an explicit choice: with nothing
 * chosen it says so, and nothing is assumed. A popover on a wide screen, a
 * bottom sheet on a phone.
 */
export function ShopPicker({
  shops,
  value,
  onChange,
  label,
  emptyLabel = "Choose a shop…",
  size = "sm",
  disabled = false,
}: {
  shops: ShopChoice[];
  value: string | null;
  onChange: (id: string) => void;
  /** For screen readers and the sheet's heading: what this sets. */
  label: string;
  emptyLabel?: string;
  size?: "sm" | "xs";
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const chosen = shops.find((s) => s.id === value) ?? null;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const text = size === "xs" ? "text-xs" : "text-sm";
  return (
    <div ref={root} className="relative inline-block max-w-full">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${chosen ? chosen.name : emptyLabel}`}
        className={
          `field flex min-h-[2.25rem] w-auto max-w-full items-center gap-2 py-1 text-left sm:max-w-[16rem] ${text} ` +
          (chosen ? "" : "border-amber-300 bg-amber-50")
        }
      >
        <svg aria-hidden width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-slate-500">
          <path d="M3 9l1.5-5h15L21 9M4 9v11h16V9M3 9h18M9 20v-6h6v6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span translate="no" className={"min-w-0 flex-1 truncate " + (chosen ? "font-medium text-slate-900" : "text-amber-900")}>
          {chosen ? chosen.name : emptyLabel}
        </span>
        <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-slate-400">
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <Sheet key="sheet" title={label} onClose={() => setOpen(false)} width="w-64">
          <ul role="listbox" aria-label={label} className="max-h-[60vh] overflow-y-auto py-1 sm:max-h-72">
            {shops.map((s) => (
              <li key={s.id} role="presentation">
                <button
                  type="button"
                  role="option"
                  aria-selected={s.id === value}
                  onClick={() => {
                    setOpen(false);
                    if (s.id !== value) onChange(s.id);
                  }}
                  className={
                    "flex min-h-[2.75rem] w-full items-center gap-2 px-4 py-2 text-left text-sm hover:bg-brand-50 sm:min-h-0 sm:px-3 " +
                    (s.id === value ? "font-medium text-slate-900" : "text-slate-700")
                  }
                >
                  <span translate="no" className="min-w-0 flex-1 truncate">{s.name}</span>
                  {s.id === value && <span key="tick" aria-hidden className="text-brand-600">✓</span>}
                </button>
              </li>
            ))}
          </ul>
        </Sheet>
      )}
    </div>
  );
}
