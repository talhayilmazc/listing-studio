"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { requestsLow } from "@/lib/requestsLow";
import type { Quota } from "@/lib/types";

/**
 * "Etsy requests today": the remaining-quota indicator Etsy's API terms ask for. It lives on the Shops
 * page (fix/onboarding-followups, Part F; it was a card on Home that a new seller read as something to
 * act on). Home shows only `RequestsLow`, and only when under a fifth of the day's requests is left.
 */
export function EtsyRequestsCard() {
  const [quota, setQuota] = useState<Quota | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.quota().then(setQuota).catch(() => setFailed(true));
  }, []);
  if (failed) return null;
  if (!quota) {
    return (
      <section className="card space-y-3 p-6" aria-hidden>
        <div className="h-3 w-24 animate-pulse rounded bg-slate-100" />
        <div className="h-8 w-20 animate-pulse rounded bg-slate-100" />
        <div className="h-1.5 w-full animate-pulse rounded-full bg-slate-100" />
      </section>
    );
  }
  const c = quota.ceiling;
  const pct = c.limit > 0 ? Math.min(100, (c.used / c.limit) * 100) : 0;
  const low = requestsLow(quota);

  return (
    <section className="card p-6" data-testid="etsy-requests">
      <div className="flex items-baseline justify-between">
        <h2 className="label mb-0">Etsy requests today</h2>
        <span translate="no" className="text-xs tabular-nums text-slate-400">{quota.usage_date}</span>
      </div>

      <p className="mt-3 flex items-baseline gap-1.5">
        <span translate="no" className={"font-display text-3xl font-normal tabular-nums " + (low ? "text-amber-700" : "text-slate-900")}>
          {c.remaining.toLocaleString()}
        </span>
        <span translate="no" className="text-sm text-slate-500">{`of ${c.limit.toLocaleString()} left`}</span>
      </p>

      <div className="mt-4">
        <div className="mb-1.5 flex items-baseline justify-between text-xs">
          <span className="text-slate-500">Your account, all shops</span>
          <span translate="no" className="tabular-nums text-slate-500">
            <span className="font-medium text-slate-700">{c.used.toLocaleString()}</span><span>{` / ${c.limit.toLocaleString()}`}</span>
          </span>
        </div>
        <div className="progress">
          <div className={"h-full rounded-full transition-all " + (low ? "bg-amber-600" : "bg-brand-600")} style={{ width: pct + "%" }} />
        </div>
      </div>

      <p translate="no" className="mt-4 text-xs text-slate-500">
        {`Drafts, publishing, Replace images, new shop sections and SKU updates count. Setting a profile up in your shops and keeping them current do not. Resets at ${c.resets_label} (00:00 UTC).`}
      </p>
    </section>
  );
}

/** Home's only word about Etsy requests: shown when under 20% of the day's are left, with no numbers. */
export function RequestsLow({ quota }: { quota: Quota | null }) {
  if (!quota || !requestsLow(quota)) return null;
  return (
    <p role="status" className="card border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" data-testid="requests-low">
      <span translate="no">{`Less than a fifth of today's Etsy requests is left for your account. New drafts and publishing may wait until they reset at ${quota.ceiling.resets_label}.`}</span>{" "}
      <Link href="/connect#etsy-requests" className="tap font-medium underline">See Shops</Link>
    </p>
  );
}
