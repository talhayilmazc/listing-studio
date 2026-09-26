"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { matchesProfile } from "@/lib/profileSearch";
import type { Profile } from "@/lib/types";

const TEMPLATE: Record<string, string> = { apparel: "Apparel", digital_products: "Digital" };

/**
 * Choosing a profile among many (v7 §D1): a button that opens a search box and
 * the list. Typing filters by profile name, template and the reference
 * listing's title; arrows and Enter pick, Escape closes. With several shops the
 * list is grouped by shop, since a profile decides which shop a listing is for.
 */
export function ProfilePicker({
  profiles,
  value,
  onChange,
  emptyLabel,
  label,
  size = "sm",
  disabled = false,
}: {
  profiles: Profile[];
  value: string | null;
  onChange: (id: string | null) => void;
  /** What choosing nothing means here ("Choose…", "Own profile"). */
  emptyLabel: string;
  /** For screen readers: what this picker sets. */
  label: string;
  size?: "sm" | "xs";
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const chosen = profiles.find((p) => p.id === value) ?? null;
  const shops = new Set(profiles.map((p) => p.shop_name ?? "Shop")).size;

  const shown = useMemo(
    () => profiles.filter((p) => matchesProfile(p, q, p.reference_title)),
    [profiles, q],
  );
  // Row 0 is "nothing"; then the matches, in shop order when grouped.
  const rows: (Profile | null)[] = useMemo(() => {
    const sorted = shops > 1 ? [...shown].sort((a, b) => (a.shop_name ?? "").localeCompare(b.shop_name ?? "")) : shown;
    return [null, ...sorted];
  }, [shown, shops]);

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
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  }

  const text = size === "xs" ? "text-xs" : "text-sm";
  let lastShop: string | null = null;

  return (
    <div ref={root} className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setOpen((o) => !o);
          setActive(0);
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${chosen ? chosen.name : emptyLabel}`}
        className={`field flex w-auto max-w-[18rem] items-center gap-2 py-1 text-left ${text}`}
      >
        <span className={"min-w-0 flex-1 truncate " + (chosen ? "text-slate-900" : "text-slate-500")}>
          {chosen ? chosen.name : emptyLabel}
        </span>
        <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-slate-400">
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div key="popover" className="absolute left-0 top-full z-30 mt-1 w-[22rem] max-w-[90vw] rounded-lg border border-slate-200 bg-white shadow-lg">
          <div className="border-b border-slate-100 p-2">
            <input
              type="search"
              autoFocus
              className="field py-1.5 text-sm"
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
              <span translate="no">{profiles.length}</span>
              <span> profiles</span>
            </p>
          </div>
          <ul ref={listRef} role="listbox" aria-label={label} className="max-h-72 overflow-y-auto py-1">
            {rows.map((p, i) => {
              const shop = p ? p.shop_name ?? "Shop" : null;
              const heading = shops > 1 && p && shop !== lastShop;
              if (p) lastShop = shop;
              const selected = p ? p.id === value : value === null;
              return (
                <li key={p ? p.id : "none"} role="presentation">
                  {heading && (
                    <p key="heading" className="px-3 pb-0.5 pt-2 text-[10px] font-medium uppercase tracking-[0.1em] text-slate-400">
                      {shop}
                    </p>
                  )}
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    data-row={i}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => pick(p)}
                    className={
                      "flex w-full flex-col items-start px-3 py-1.5 text-left text-sm " +
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
            {shown.length === 0 && (
              <li key="none-match" className="px-3 py-2 text-xs text-slate-400">
                No profile matches.
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
