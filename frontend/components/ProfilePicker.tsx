"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { matchesProfile } from "@/lib/profileSearch";
import type { Profile } from "@/lib/types";
import { Sheet } from "./Sheet";

const TEMPLATE: Record<string, string> = { apparel: "Apparel", digital_products: "Digital" };

/**
 * Choosing a profile of ONE shop (v7 §D1, shop first since Priority 2): a
 * button that opens a search box and that shop's profiles. The shop is chosen
 * before the profile, so the list is never a mix of every shop's profiles;
 * with no shop chosen the picker says so and stays closed. Typing filters by
 * profile name, template and the reference listing's title; arrows and Enter
 * pick, Escape closes. A popover on a wide screen, a bottom sheet on a phone.
 */
export function ProfilePicker({
  profiles,
  shopId,
  shopName,
  value,
  onChange,
  emptyLabel,
  label,
  size = "sm",
  disabled = false,
}: {
  /** Any of the account's profiles; only ``shopId``'s are offered. */
  profiles: Profile[];
  /** The shop whose profiles may be chosen; null = none chosen yet. */
  shopId: string | null;
  shopName?: string | null;
  value: string | null;
  onChange: (id: string | null) => void;
  /** What choosing nothing means here ("Choose…", "Own profile"). */
  emptyLabel: string;
  /** For screen readers and the sheet's heading: what this picker sets. */
  label: string;
  size?: "sm" | "xs";
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const own = useMemo(() => profiles.filter((p) => p.connection_id === shopId), [profiles, shopId]);
  // A value from another shop is never shown as if it were this shop's.
  const chosen = own.find((p) => p.id === value) ?? null;

  const shown = useMemo(() => own.filter((p) => matchesProfile(p, q, p.reference_title)), [own, q]);
  // Row 0 is "nothing"; then the matches.
  const rows: (Profile | null)[] = useMemo(() => [null, ...shown], [shown]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-row="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function pick(p: Profile | null) {
    onChange(p ? p.id : null);
    setOpen(false);
    setQ("");
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(rows.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (rows[active] !== undefined) pick(rows[active]);
    }
  }

  const text = size === "xs" ? "text-xs" : "text-sm";
  const noShop = shopId === null;
  const shownLabel = noShop ? "Choose a shop first" : chosen ? chosen.name : emptyLabel;

  return (
    <div ref={root} className="relative inline-block max-w-full">
      <button
        type="button"
        disabled={disabled || noShop}
        onClick={() => {
          setOpen((o) => !o);
          setActive(0);
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${shownLabel}`}
        title={noShop ? "Profiles belong to a shop: choose the shop, then one of its profiles." : undefined}
        className={`field flex min-h-[2.25rem] w-auto max-w-full items-center gap-2 py-1 text-left disabled:cursor-not-allowed sm:max-w-[18rem] ${text}`}
      >
        <span className={"min-w-0 flex-1 truncate " + (chosen ? "text-slate-900" : "text-slate-500")}>{shownLabel}</span>
        <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-slate-400">
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <Sheet key="sheet" title={label} onClose={() => setOpen(false)}>
          <div className="border-b border-slate-100 p-3 sm:p-2">
            <input
              type="search"
              autoFocus
              className="field py-2 text-base sm:py-1.5 sm:text-sm"
              placeholder="Search name, template or listing title…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setActive(e.target.value ? 1 : 0);
              }}
              onKeyDown={onKey}
              aria-label={`Search profiles for ${label.toLowerCase()}`}
            />
            <p className="mt-1 px-0.5 text-[11px] text-slate-400">
              <span translate="no">{shown.length}</span>
              <span>{` of `}</span>
              <span translate="no">{own.length}</span>
              <span>{shopName ? " profiles of " : " profiles of this shop"}</span>
              {shopName && <span key="shop" translate="no" className="font-medium text-slate-500">{shopName}</span>}
            </p>
          </div>
          <ul ref={listRef} role="listbox" aria-label={label} className="max-h-[55vh] overflow-y-auto py-1 sm:max-h-72">
            {rows.map((p, i) => {
              const selected = p ? p.id === value : chosen === null;
              return (
                <li key={p ? p.id : "none"} role="presentation">
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    data-row={i}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => pick(p)}
                    className={
                      "flex min-h-[2.75rem] w-full flex-col items-start justify-center px-4 py-2 text-left text-sm sm:min-h-0 sm:px-3 sm:py-1.5 " +
                      (i === active ? "bg-brand-50" : "") +
                      (selected ? " font-medium" : "")
                    }
                  >
                    {p ? (
                      <>
                        <span className="flex w-full items-center gap-2">
                          <span className="min-w-0 flex-1 truncate text-slate-900">{p.name}</span>
                          <span className="shrink-0 rounded bg-slate-100 px-1.5 text-[10px] text-slate-500">
                            {TEMPLATE[p.content_template] ?? p.content_template}
                          </span>
                        </span>
                        {p.reference_title && (
                          <span key="ref" className="w-full truncate text-xs text-slate-400" title={p.reference_title}>
                            {p.reference_title}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-slate-500">{emptyLabel}</span>
                    )}
                  </button>
                </li>
              );
            })}
            {own.length === 0 && (
              <li key="no-profiles" className="px-4 py-3 text-xs text-amber-800 sm:px-3">
                This shop has no confirmed profile yet. Create one on the Profiles page.
              </li>
            )}
            {own.length > 0 && shown.length === 0 && (
              <li key="none-match" className="px-4 py-2 text-xs text-slate-400 sm:px-3">
                No profile matches.
              </li>
            )}
          </ul>
        </Sheet>
      )}
    </div>
  );
}
