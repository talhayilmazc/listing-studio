"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PatternListing } from "@/lib/types";

/**
 * "Model on one of my listings" (v7 §B): the new listing keeps the title and
 * tag pattern of one of the seller's OWN listings, with this design's subject.
 * Only the seller's own shop is offered; every listing links back to Etsy.
 */
export function PatternPicker({
  chosen,
  listings,
  onChoose,
  busy,
}: {
  chosen: number | null;
  listings: PatternListing[] | null;
  onChoose: (listingId: number | null) => Promise<void>;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const current = chosen != null ? listings?.find((l) => l.listing_id === chosen) : null;
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (listings ?? []).filter((l) =>
    words.every((w) => `${l.title ?? ""} ${l.tags.join(" ")}`.toLowerCase().includes(w)),
  );

  return (
    <div className="mt-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-slate-500">Pattern</span>
        {chosen != null ? (
          <>
            <span className="max-w-[28rem] truncate text-slate-800" title={current?.title ?? undefined}>
              modelled on &ldquo;{current?.title ?? `listing ${chosen}`}&rdquo;
            </span>
            {current && (
              <a href={current.url} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
                ↗
              </a>
            )}
            <button type="button" className="underline hover:text-slate-900" onClick={() => setOpen((v) => !v)} disabled={busy}>
              change
            </button>
            <button type="button" className="underline hover:text-slate-900" onClick={() => onChoose(null)} disabled={busy}>
              clear
            </button>
          </>
        ) : (
          <button type="button" className="text-brand-700 underline hover:text-brand-800" onClick={() => setOpen((v) => !v)} disabled={busy}>
            Model on one of my listings…
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-2">
          <p className="mb-1.5 text-slate-500">
            Keeps the chosen listing&apos;s title and tag pattern; the subject comes from this design.
            Only your own shop&apos;s active listings.
          </p>
          <input
            type="search"
            className="field mb-2 w-full py-1 text-xs"
            placeholder="Search your listings…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            autoFocus
          />
          {listings === null ? (
            <p className="text-slate-400">Loading your listings…</p>
          ) : shown.length === 0 ? (
            <p className="text-slate-400">No active listing matches. Your shop&apos;s listings refresh every six hours.</p>
          ) : (
            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {shown.slice(0, 60).map((l) => (
                <li key={l.listing_id} className="flex items-center gap-2 rounded bg-white p-1.5">
                  {l.thumbnail_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={l.thumbnail_url} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
                  ) : (
                    <span className="h-9 w-9 shrink-0 rounded bg-slate-100" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-slate-800" title={l.title ?? undefined}>
                      {l.title}
                    </span>
                    <span className="block truncate text-slate-400">{l.tags.slice(0, 6).join(" · ")}</span>
                  </span>
                  <a href={l.url} target="_blank" rel="noreferrer" className="shrink-0 text-brand-700 hover:underline" title="View on Etsy">
                    ↗
                  </a>
                  <button
                    type="button"
                    className="btn-secondary shrink-0 px-2 py-0.5 text-xs"
                    onClick={async () => {
                      await onChoose(l.listing_id);
                      setOpen(false);
                    }}
                    disabled={busy}
                  >
                    Use
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** The seller's own listings for patterns, loaded once per page. */
export function usePatternListings(enabled: boolean): PatternListing[] | null {
  const [rows, setRows] = useState<PatternListing[] | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    api
      .patternListings()
      .then((r) => !cancelled && setRows(r))
      .catch(() => !cancelled && setRows([]));
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return rows;
}
