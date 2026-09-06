"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BatchSummary, Profile, Quota, ShopSummary } from "@/lib/types";

/**
 * Metric strip (docs/ui-direction-v2.md §3): a band of real figures above the
 * page content — serif numbers, muted labels, each with the period it covers and
 * a comparison where one is computable.
 *
 * Reads only side-effect-free endpoints. `/shop/summary` serves the cached
 * counts without the sync `/shop/listings` would enqueue, so a strip on every
 * page never spends Etsy quota.
 */

// Utility pages carry no shop context.
const HIDDEN = [/^\/terms$/, /^\/privacy$/, /^\/connect$/];

export function MetricStrip() {
  const pathname = usePathname() ?? "/";
  const [quota, setQuota] = useState<Quota | null>(null);
  const [shop, setShop] = useState<ShopSummary | null>(null);
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);

  const hidden = HIDDEN.some((re) => re.test(pathname));

  useEffect(() => {
    if (hidden) return;
    let cancelled = false;
    api.quota().then((q) => !cancelled && setQuota(q)).catch(() => {});
    api.shopSummary().then((s) => !cancelled && setShop(s)).catch(() => {});
    api.listBatches().then((b) => !cancelled && setBatches(b)).catch(() => setBatches([]));
    api.listProfiles().then((p) => !cancelled && setProfiles(p)).catch(() => setProfiles([]));
    return () => {
      cancelled = true;
    };
  }, [hidden]);

  if (hidden) return null;

  const activeProfiles = profiles?.filter((p) => p.confirmed).length ?? null;
  const awaitingReview =
    batches?.reduce((n, b) => n + Math.max(0, b.processed_count - b.approved_count), 0) ?? null;

  return (
    <div className="border-b border-slate-200">
      <dl className="mx-auto grid w-full max-w-[1800px] grid-cols-2 lg:grid-cols-4">
        <Cell
          label="Published"
          period="this month"
          value={shop?.published_this_month ?? null}
          delta={
            shop ? changePct(shop.published_this_month, shop.published_last_month) : null
          }
          deltaNote="vs last month"
        />
        <Cell
          label="Draft listings"
          period="awaiting publish"
          value={shop?.draft ?? null}
          secondary={shop ? `${shop.active.toLocaleString()} live` : null}
        />
        <QuotaCell quota={quota} />
        <Cell
          label="Active profiles"
          period="in your library"
          value={activeProfiles}
          secondary={
            awaitingReview === null
              ? null
              : `${awaitingReview.toLocaleString()} designs awaiting review`
          }
        />
      </dl>
    </div>
  );
}

/**
 * Mean calls per day across the completed days of the window. Today is excluded
 * on purpose: a morning's two requests against yesterday's full day reads as a
 * collapse when nothing has happened yet.
 */
function dailyAverage(history: Quota["history"]): number | null {
  const complete = (history ?? []).slice(0, -1);
  if (complete.length === 0) return null;
  return Math.round(complete.reduce((n, d) => n + d.count, 0) / complete.length);
}

/** Percent change, or null when there is no meaningful base to compare against. */
function changePct(now: number, before: number): number | null {
  if (before <= 0) return null;
  return Math.round(((now - before) / before) * 100);
}

// Hairlines between cells only, never a leading edge: left borders on the 2nd
// cell of each row (plus the 3rd once the row holds four), top borders only on
// the wrapped second row at narrow widths.
const CELL =
  "border-slate-200 px-6 py-5 lg:px-8 " +
  "[&:nth-child(even)]:border-l lg:[&:nth-child(3)]:border-l " +
  "[&:nth-child(n+3)]:border-t lg:[&:nth-child(n+3)]:border-t-0";

function Label({ label, period }: { label: string; period: string }) {
  return (
    <dt className="flex flex-wrap items-baseline gap-x-1.5 text-xs">
      <span className="font-medium text-slate-600">{label}</span>
      <span className="text-slate-400">{period}</span>
    </dt>
  );
}

function Cell({
  label,
  period,
  value,
  delta,
  deltaNote,
  secondary,
}: {
  label: string;
  period: string;
  value: number | null;
  delta?: number | null;
  deltaNote?: string;
  secondary?: string | null;
}) {
  return (
    <div className={CELL}>
      <Label label={label} period={period} />
      <dd className="mt-1.5 flex items-baseline gap-2.5">
        {value === null ? (
          <span className="inline-block h-8 w-14 animate-pulse rounded bg-slate-100" />
        ) : (
          <span className="font-display text-3xl leading-none tabular-nums text-slate-900">
            {value.toLocaleString()}
          </span>
        )}
        {delta != null && delta !== 0 && (
          <span
            className={
              "flex items-baseline gap-0.5 text-xs font-medium tabular-nums " +
              (delta > 0 ? "text-emerald-700" : "text-slate-500")
            }
            title={deltaNote}
          >
            <span aria-hidden>{delta > 0 ? "↑" : "↓"}</span>
            {Math.abs(delta)}%
          </span>
        )}
      </dd>
      <p className="mt-1.5 h-4 text-xs text-slate-400">
        {delta != null && delta !== 0 ? deltaNote : (secondary ?? "")}
      </p>
    </div>
  );
}

/**
 * Quota is the anchor of the strip: it is the scarce resource, so it carries the
 * sunken surface and the full-height 7-day chart.
 */
function QuotaCell({ quota }: { quota: Quota | null }) {
  const low = quota ? quota.tenant_remaining < quota.tenant_limit * 0.1 : false;
  const average = quota ? dailyAverage(quota.history) : null;

  return (
    <div className={CELL + " bg-slate-50"}>
      <Label label="API quota" period="remaining today" />
      <dd className="mt-1.5 flex items-end justify-between gap-4">
        <div>
          {quota === null ? (
            <span className="inline-block h-8 w-28 animate-pulse rounded bg-slate-100" />
          ) : (
            <span
              className={
                "font-display text-3xl leading-none tabular-nums " +
                (low ? "text-amber-700" : "text-slate-900")
              }
            >
              {quota.tenant_remaining.toLocaleString()}
              <span className="text-lg text-slate-400">
                {" / "}
                {quota.tenant_limit.toLocaleString()}
              </span>
            </span>
          )}
          <p className="mt-1.5 h-4 text-xs text-slate-400">
            {quota === null
              ? ""
              : average != null
                ? `${quota.tenant_used.toLocaleString()} used today · avg ${average.toLocaleString()}/day over 7 days`
                : `${quota.tenant_used.toLocaleString()} used today`}
          </p>
        </div>
        {quota && <UsageChart history={quota.history} />}
      </dd>
    </div>
  );
}

/** Seven days of calls, oldest to newest, today picked out in the accent. */
function UsageChart({ history }: { history: Quota["history"] }) {
  if (!history?.length) return null;
  const max = Math.max(1, ...history.map((d) => d.count));
  const H = 64; // tall enough to read a shape, not a hairline

  return (
    <span
      className="flex shrink-0 items-end gap-1"
      style={{ height: H }}
      aria-label={`API calls over the last ${history.length} days`}
    >
      {history.map((d, i) => {
        const last = i === history.length - 1;
        return (
          <span
            key={d.date}
            title={`${d.date}: ${d.count.toLocaleString()} calls`}
            className={
              "w-2 rounded-sm " +
              (last ? "bg-brand-600" : d.count ? "bg-slate-300" : "bg-slate-200")
            }
            style={{ height: Math.max(2, Math.round((d.count / max) * H)) + "px" }}
          />
        );
      })}
    </span>
  );
}
