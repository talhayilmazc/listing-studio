"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Allowance, BatchShops } from "@/lib/types";

import { Txt } from "@/components/Txt";
const PERIOD_WORD: Record<string, string> = { daily: "today", weekly: "this week", monthly: "this month" };

/**
 * Said before Generate writes anything: how many listings, and what it uses of the allowance
 * ("new": each counts as one listing generated; "rewrite": listings created by hand, so each is
 * a rewrite of its design, free for the first ones per design this period). Nothing is written
 * until the seller presses Write.
 */
export function GenerateConfirm({ count, kind, onConfirm, onCancel }: {
  count: number;
  kind: "new" | "rewrite";
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  useEffect(() => {
    api.myAllowance().then(setAllowance).catch(() => setAllowance(null));
  }, []);
  const left = allowance ? Math.max(0, allowance.amount - allowance.used - (allowance.pending ?? 0)) : null;
  const period = allowance ? PERIOD_WORD[allowance.period] ?? "this period" : "";
  const free = allowance?.free_rewrites ?? 3;
  return (
    <div role="alertdialog" aria-label="Generate with AI" className="card space-y-2 border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" data-testid="generate-confirm">
      <p className="font-medium" translate="no">{`Write ${count} listing${count === 1 ? "" : "s"} with AI?`}</p>
      <p className="text-xs" translate="no">
        {kind === "new"
          ? `Each counts as one listing generated: ${count} of your ${left ?? "…"} left ${period}.`
          : `These were created by hand with nothing typed in yet, so each is a rewrite of its design: the first ${free} of a design ${period} are free, then each counts as one listing generated (${left ?? "…"} left ${period}). Listings you typed into are never written over.`}
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={onConfirm} data-testid="generate-confirm-go">
          <span translate="no">{`Write ${count}`}</span>
        </button>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={onCancel}>Not now</button>
      </div>
    </div>
  );
}

/** "These designs are already written in batch X": a batch made by uploading the same designs
 *  again (to post them in another shop) says where their text is, so nothing is written twice. */
export function WrittenElsewhere({ data }: { data: BatchShops | null }) {
  const found = data?.written_elsewhere ?? [];
  if (!found.length) return null;
  return (
    <div role="status" className="card space-y-1 border-brand-200 bg-brand-50 p-3 text-sm text-brand-950" data-testid="written-elsewhere">
      {found.map((w) => (
        <p key={w.batch_id}>
          <span translate="no">
            <span><span>{`${w.skus.join(", ")}${w.count > w.skus.length ? ` and ${w.count - w.skus.length} more` : ""}: already written in the batch "${w.name}"`}</span>
            <Txt>{w.shops.length ? ` (on Etsy in ${w.shops.join(", ")})` : ""}</Txt></span>
          </span>
          <span>. To post them in another shop, open that batch and choose the shop at the top: nothing needs writing again. </span>
          <Link href={`/batches/${w.batch_id}`} className="tap font-medium text-brand-700 underline">Open that batch</Link>
        </p>
      ))}
    </div>
  );
}
