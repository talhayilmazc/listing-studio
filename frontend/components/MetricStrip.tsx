"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BatchSummary, Profile, Quota } from "@/lib/types";

/**
 * Metric strip (docs/ui-direction-v2.md §3): a thin band of real figures above
 * the page content — serif numbers, small muted labels, a 7-day sparkline for
 * quota built from the additive `history` field.
 *
 * Deliberately reads only side-effect-free endpoints. `/api/shop/listings`
 * enqueues a shop sync when its 6h cache is stale, so calling it from a strip
 * present on every page would multiply those syncs and spend Etsy quota.
 */

// Utility pages carry no shop context.
const HIDDEN = [/^\/terms$/, /^\/privacy$/, /^\/connect$/];

export function MetricStrip() {
  const pathname = usePathname() ?? "/";
  const [quota, setQuota] = useState<Quota | null>(null);
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);

  const hidden = HIDDEN.some((re) => re.test(pathname));

  useEffect(() => {
    if (hidden) return;
    let cancelled = false;
    api.quota().then((q) => !cancelled && setQuota(q)).catch(() => {});
    api.listBatches().then((b) => !cancelled && setBatches(b)).catch(() => setBatches([]));
    api.listProfiles().then((p) => !cancelled && setProfiles(p)).catch(() => setProfiles([]));
    return () => {
      cancelled = true;
    };
  }, [hidden]);

  if (hidden) return null;

  const approved = batches?.reduce((n, b) => n + b.approved_count, 0) ?? null;
  const processed = batches?.reduce((n, b) => n + b.processed_count, 0) ?? null;
  const activeProfiles = profiles?.filter((p) => p.confirmed).length ?? null;

  return (
    <div className="border-b border-slate-200">
      <dl className="mx-auto grid w-full max-w-[1800px] grid-cols-2 lg:grid-cols-4">
        <Cell label="Approved listings" value={approved} />
        <Cell label="Designs processed" value={processed} />
        <QuotaCell quota={quota} />
        <Cell label="Active profiles" value={activeProfiles} />
      </dl>
    </div>
  );
}

// Hairlines between cells only, never a leading edge: left borders on the 2nd
// cell of each row (plus the 3rd once the row holds four), top borders only on
// the wrapped second row at narrow widths.
const CELL =
  "border-slate-200 px-6 py-4 lg:px-8 " +
  "[&:nth-child(even)]:border-l lg:[&:nth-child(3)]:border-l " +
  "[&:nth-child(n+3)]:border-t lg:[&:nth-child(n+3)]:border-t-0";

function Cell({ label, value }: { label: string; value: number | null }) {
  return (
    <div className={CELL}>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-display text-3xl leading-none text-slate-900">
        {value === null ? (
          <span className="inline-block h-7 w-12 animate-pulse rounded bg-slate-100 align-bottom" />
        ) : (
          <span className="tabular-nums">{value.toLocaleString()}</span>
        )}
      </dd>
    </div>
  );
}

function QuotaCell({ quota }: { quota: Quota | null }) {
  return (
    <div className={CELL}>
      <dt className="text-xs text-slate-500">Today&rsquo;s quota</dt>
      <dd className="mt-0.5 flex items-end justify-between gap-4">
        {quota === null ? (
          <span className="inline-block h-7 w-24 animate-pulse rounded bg-slate-100" />
        ) : (
          <span className="font-display text-3xl leading-none tabular-nums text-slate-900">
            {quota.tenant_remaining.toLocaleString()}
            <span className="text-lg text-slate-400">
              {" / "}
              {quota.tenant_limit.toLocaleString()}
            </span>
          </span>
        )}
        {quota && <Sparkline history={quota.history} />}
      </dd>
    </div>
  );
}

/** Seven days of calls, oldest to newest, today picked out in the accent. */
function Sparkline({ history }: { history: Quota["history"] }) {
  if (!history?.length) return null;
  const max = Math.max(1, ...history.map((d) => d.count));
  return (
    <span
      className="flex h-7 shrink-0 items-end gap-[3px]"
      title={history.map((d) => `${d.date}: ${d.count.toLocaleString()} calls`).join("\n")}
      aria-label={`API calls over the last ${history.length} days`}
    >
      {history.map((d, i) => {
        const last = i === history.length - 1;
        return (
          <span
            key={d.date}
            className={
              "w-1.5 rounded-sm " + (last ? "bg-brand-600" : d.count ? "bg-slate-300" : "bg-slate-200")
            }
            style={{ height: Math.max(2, Math.round((d.count / max) * 28)) + "px" }}
          />
        );
      })}
    </span>
  );
}
