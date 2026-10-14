"use client";

import { useState } from "react";
import type { Check, Checklist } from "@/lib/types";

/**
 * The search-readiness checklist on a review card: collapsed to its summary
 * ("7 of 8 checks pass"), open to each check with its reason.
 *
 * Every word of a check, the heading, the note and the summary come from the
 * server (backend pipeline/checklist.py), which works it out from the listing's
 * own text and settings with the app's existing rules. Nothing here judges a
 * rule, so there is no second copy to drift. It never blocks approval.
 */

const STATUS: Record<Check["status"], { word: string; dot: string; text: string }> = {
  pass: { word: "Meets", dot: "bg-emerald-500", text: "text-emerald-700" },
  warn: { word: "Check", dot: "bg-amber-500", text: "text-amber-800" },
  fail: { word: "Not met", dot: "bg-rose-500", text: "text-rose-700" },
};

export function SearchChecklist({ checklist, updating = false }: { checklist: Checklist | null; updating?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!checklist) return null;
  const worst = checklist.checks.some((c) => c.status === "fail")
    ? "fail"
    : checklist.checks.some((c) => c.status === "warn")
      ? "warn"
      : "pass";
  return (
    <section className="rounded-md border border-slate-200 bg-slate-50/60" data-testid="search-checklist">
      <button
        type="button"
        className="flex min-h-[2.75rem] w-full items-center gap-2.5 px-3 py-2 text-left"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-testid="search-checklist-toggle"
      >
        <span aria-hidden className={"h-2 w-2 shrink-0 rounded-full " + STATUS[worst].dot} />
        {/* On a phone the summary sits under the heading, so neither wraps. */}
        <span className="min-w-0 flex-1 sm:flex sm:items-baseline sm:justify-between sm:gap-3">
          <span className="block text-sm font-medium text-slate-900">{checklist.heading}</span>
          {/* Only opacity changes while the edited text is checked again: nothing moves. */}
          <span
            className={"block text-xs text-slate-600 transition-opacity " + (updating ? "opacity-50" : "opacity-100")}
            translate="no"
            data-testid="search-checklist-summary"
          >
            {checklist.summary}
          </span>
        </span>
        <svg
          aria-hidden
          viewBox="0 0 20 20"
          className={"h-4 w-4 shrink-0 fill-slate-400 transition-transform " + (open ? "rotate-180" : "")}
        >
          <path d="M5.3 7.3a1 1 0 0 1 1.4 0L10 10.6l3.3-3.3a1 1 0 1 1 1.4 1.4l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 0 1 0-1.4Z" />
        </svg>
      </button>
      <p className="px-3 pb-2 text-xs text-slate-500">{checklist.note}</p>
      {open && (
        <ul key="checks" className="space-y-2 border-t border-slate-200 px-3 py-2.5" translate="no" data-testid="search-checklist-list">
          {checklist.checks.map((check) => (
            <li key={check.key} className="flex min-w-0 gap-2.5 text-xs" data-testid="search-check" data-status={check.status}>
              <span className={"flex w-16 shrink-0 items-center gap-1.5 font-medium " + STATUS[check.status].text}>
                <span aria-hidden className={"h-1.5 w-1.5 shrink-0 rounded-full " + STATUS[check.status].dot} />
                <span>{STATUS[check.status].word}</span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-slate-800">{check.label}</span>
                <span className="block break-words text-slate-600">{check.reason}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
