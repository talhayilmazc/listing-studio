"use client";

import Link from "next/link";
import { useState } from "react";
import type { Destination, SendState } from "@/lib/types";
import { destinationOptions, isChosen, toggle } from "@/lib/whereWhen";
import { Sheet } from "./Sheet";

/**
 * Where listings go (B4): a button that opens the account's shop groups first (with a
 * search), then single shops under "or one shop", and a link to create groups. Several can
 * be ticked: every listing goes to every one of their shops, each shop once. ``follow``
 * adds "Same as the others" for one listing's own destination. A popover on a wide screen,
 * a bottom sheet on a phone.
 */
export function DestinationPicker({
  state,
  value,
  label,
  onChange,
  follow,
  disabled = false,
  compact = false,
  title = "Send to",
}: {
  state: SendState;
  value: Destination | null;
  /** What the button shows ("GROUP 1"). */
  label: string;
  onChange: (next: Destination | null) => void;
  /** One listing: "Same as the others (GROUP 1)" puts it back on the batch's choice. */
  follow?: string;
  disabled?: boolean;
  /** A text link ("change") in place of a field. */
  compact?: boolean;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const { groups, shops } = destinationOptions(state, q);
  const many = state.groups.length + state.shops.length > 6;
  const row = "flex min-h-[44px] w-full cursor-pointer items-center gap-2 px-4 py-2 text-left text-sm sm:min-h-0 sm:px-3 sm:py-1.5";

  return (
    <div className="relative inline-block max-w-full">
      {compact ? (
        <button type="button" className="tap text-brand-700 underline" disabled={disabled} onClick={() => setOpen((o) => !o)}
          aria-haspopup="dialog" aria-expanded={open}>
          change
        </button>
      ) : (
        <button type="button" disabled={disabled} onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open}
          aria-label={`${title}: ${label}`} data-testid="send-to"
          className="field flex min-h-[2.25rem] w-auto max-w-full items-center gap-2 py-1 text-left text-sm sm:max-w-[22rem]">
          <span translate="no" className="min-w-0 flex-1 truncate font-medium text-slate-900">{label}</span>
          <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-slate-400">
            <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
      {open && (
        <Sheet key="sheet" title={title} onClose={() => setOpen(false)}>
          {many && (
            <div key="search" className="border-b border-slate-100 p-3 sm:p-2">
              <input type="search" autoFocus className="field py-2 text-base sm:py-1.5 sm:text-sm" placeholder="Search groups and shops…"
                value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search groups and shops" />
            </div>
          )}
          <div className="max-h-[55vh] overflow-y-auto py-1 sm:max-h-80">
            {follow !== undefined && (
              <button key="follow" type="button" className={row + (value === null ? " font-medium" : "")}
                onClick={() => { onChange(null); setOpen(false); }}>
                <span className="min-w-0 flex-1 truncate"><span>Same as the others</span><span translate="no" className="text-slate-500">{` (${follow})`}</span></span>
              </button>
            )}
            <p className="px-4 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:px-3">Groups</p>
            {groups.map((g) => (
              <label key={g.id} className={row}>
                <input type="checkbox" className="h-4 w-4" checked={isChosen(value, "group", g.id)}
                  onChange={() => onChange(toggle(value, "group", g.id))} data-testid="send-to-group" />
                <span translate="no" className="min-w-0 flex-1 truncate text-slate-900">{g.name}</span>
                <span translate="no" className="shrink-0 text-xs text-slate-500">{`${g.shop_ids.length} shop${g.shop_ids.length === 1 ? "" : "s"}`}</span>
              </label>
            ))}
            {state.groups.length === 0 && (
              <p key="no-groups" className="px-4 py-1 text-xs text-slate-500 sm:px-3">No shop groups yet.</p>
            )}
            <Link href="/shops#groups" className="tap block px-4 py-1.5 text-xs text-brand-700 underline sm:px-3">Create shop groups</Link>
            <p className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:px-3">or one shop</p>
            {shops.map((s) => (
              <label key={s.id} className={row}>
                <input type="checkbox" className="h-4 w-4" checked={isChosen(value, "shop", s.id)}
                  onChange={() => onChange(toggle(value, "shop", s.id))} data-testid="send-to-shop" />
                <span translate="no" className="min-w-0 flex-1 truncate text-slate-900">{s.name}</span>
              </label>
            ))}
            {q && groups.length === 0 && shops.length === 0 && (
              <p key="none" className="px-4 py-2 text-xs text-slate-500 sm:px-3">Nothing matches.</p>
            )}
          </div>
          <div className="border-t border-slate-100 p-3 sm:p-2">
            <button type="button" className="btn-secondary w-full" onClick={() => setOpen(false)}>Done</button>
          </div>
        </Sheet>
      )}
    </div>
  );
}
