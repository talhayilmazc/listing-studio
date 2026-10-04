"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { relativeTime } from "@/lib/format";
import { api } from "@/lib/api";
import type { BatchSummary, Profile, Quota, ShopSummary } from "@/lib/types";
import { useSession } from "./SessionProvider";
import { useShops } from "./ShopProvider";

/**
 * Metric strip (docs/ui-direction-v2.md §3): a band of real figures above the
 * page content — serif numbers, muted labels, each with the period it covers and
 * a comparison where one is computable.
 *
 * `/shop/summary` serves the app's own publication count plus the cached shop
 * counts. When the shop has no usable cached copy it queues one refresh (upkeep,
 * one job per shop however many pages ask) and says "syncing" instead of 0.
 */

// Utility pages carry no shop context.
const HIDDEN = [/^\/terms$/, /^\/privacy$/, /^\/connect$/];

export function MetricStrip() {
  const pathname = usePathname() ?? "/";
  const [quota, setQuota] = useState<Quota | null>(null);
  const [shop, setShop] = useState<ShopSummary | null>(null);
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);

  const { account } = useSession();
  // Shop figures follow the shop selected in the rail (v5 §E); batches are the account's.
  const { selected } = useShops();
  const shopId = selected?.id ?? null;
  // /admin carries its own app-wide figures. For anyone else it is an ordinary
  // 404, so it keeps the strip every unknown route has.
  const hidden =
    HIDDEN.some((re) => re.test(pathname)) || (pathname === "/admin" && Boolean(account?.is_admin));

  useEffect(() => {
    if (hidden) return;
    let cancelled = false;
    api.quota(shopId).then((q) => !cancelled && setQuota(q)).catch(() => {});
    // While the shop is syncing, look again a few times so "syncing" turns into
    // numbers without a reload. Bounded: a sync that cannot finish is not polled forever.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loadShop = (triesLeft: number) =>
      api
        .shopSummary(shopId)
        .then((s) => {
          if (cancelled) return;
          setShop(s);
          if (s.syncing && triesLeft > 0) timer = setTimeout(() => loadShop(triesLeft - 1), 10_000);
        })
        .catch(() => {});
    loadShop(3);
    api.listBatches().then((b) => !cancelled && setBatches(b)).catch(() => setBatches([]));
    api.listProfiles(shopId).then((p) => !cancelled && setProfiles(p)).catch(() => setProfiles([]));
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [hidden, shopId]);

  if (hidden) return null;

  const activeProfiles = profiles?.filter((p) => p.confirmed).length ?? null;
  const awaitingReview =
    batches?.reduce((n, b) => n + Math.max(0, b.processed_count - b.approved_count), 0) ?? null;

  return (
    <div className="border-b border-slate-200">
      <dl className="mx-auto grid w-full max-w-[1800px] grid-cols-2 lg:grid-cols-4">
        {/* Two different counts, labelled apart. The headline is what the seller
            published through the app, from the app's own records (always known).
            The line under it is every listing that went live in the shop, from
            the cached copy of the shop: "syncing" when that copy is missing, never 0. */}
        <Cell
          label="Published with the app"
          period={selected ? `this month · ${selected.name}` : "this month"}
          value={shop?.app_published_this_month ?? null}
          delta={shop ? changePct(shop.app_published_this_month, shop.app_published_last_month) : null}
          deltaNote="vs last month"
          secondary={
            shop
              ? (shop.published_known ?? shop.shop_counts_known)
                ? // The last value read, for as long as it may be shown (six hours), and how old it is.
                  `all listings published in the shop: ${shop.published_this_month.toLocaleString()}` +
                  (shop.fetched_at ? ` · updated ${relativeTime(shop.fetched_at)}` : "")
                : shop.syncing
                  ? "all listings published in the shop: syncing…"
                  : shop.fetched_at
                    ? "all listings published in the shop: unknown (last read more than 6 hours ago; open Overview to refresh)"
                    : "all listings published in the shop: not read from Etsy yet"
              : null
          }
        />
        <Cell
          label="Draft listings"
          period={selected ? `awaiting publish · ${selected.name}` : "awaiting publish"}
          value={shop ? (shop.shop_counts_known ? shop.draft : null) : null}
          secondary={shop && shop.shop_counts_known ? `${shop.active.toLocaleString()} live` : null}
          unavailable={
            shop && !shop.shop_counts_known ? (shop.syncing ? "syncing from Etsy…" : "not read from Etsy yet") : undefined
          }
        />
        <QuotaCell quota={quota} />
        <Cell
          label="Active profiles"
          period={selected ? `in ${selected.name}` : "in your library"}
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
  unavailable,
}: {
  label: string;
  period: string;
  value: number | null;
  delta?: number | null;
  deltaNote?: string;
  secondary?: string | null;
  /** Set when the underlying Etsy data has expired: show a dash, never a zero. */
  unavailable?: string;
}) {
  if (unavailable) {
    return (
      <div className={CELL}>
        <Label label={label} period={period} />
        <dd className="mt-1.5 font-display text-3xl leading-none text-slate-300">—</dd>
        <p className="mt-1.5 h-4 text-xs text-slate-400">{unavailable}</p>
      </div>
    );
  }
  return (
    <div className={CELL}>
      <Label label={label} period={period} />
      <dd className="mt-1.5 flex items-baseline gap-2.5">
        {value === null ? (
          <span className="inline-block h-8 w-14 animate-pulse rounded bg-slate-100" />
        ) : (
          <span translate="no" className="font-display text-3xl leading-none tabular-nums text-slate-900">
            {value.toLocaleString()}
          </span>
        )}
        {delta != null && delta !== 0 && (
          <span translate="no" key="span-165-8"
            className={
              "flex items-baseline gap-0.5 text-xs font-medium tabular-nums " +
              (delta > 0 ? "text-emerald-700" : "text-slate-500")
            }
            title={deltaNote}
          >
            <span aria-hidden>{delta > 0 ? "↑" : "↓"}</span>
            <span><span>{Math.abs(delta)}</span>%</span>
            {secondary && deltaNote ? <span className="ml-1 font-normal text-slate-400">{deltaNote}</span> : null}
          </span>
        )}
      </dd>
      {/* A second figure is never displaced by the comparison: the note moves up beside the arrow. */}
      <p className="mt-1.5 min-h-4 text-xs text-slate-400">
        {secondary ?? (delta != null && delta !== 0 ? deltaNote : "")}
      </p>
    </div>
  );
}

/**
 * Quota is the anchor of the strip: it is the scarce resource, so it carries the
 * sunken surface and the full-height 7-day chart.
 */
function QuotaCell({ quota }: { quota: Quota | null }) {
  const ceiling = quota?.ceiling ?? null;
  const low = ceiling ? ceiling.remaining < ceiling.limit * 0.1 : false;
  const average = quota ? dailyAverage(quota.history) : null;

  return (
    <div className={CELL + " bg-slate-50"}>
      <Label label="Etsy requests today" period="left, of your account's limit" />
      <dd className="mt-1.5 flex items-end justify-between gap-4">
        <div>
          {ceiling === null ? (
            <span className="inline-block h-8 w-28 animate-pulse rounded bg-slate-100" />
          ) : (
            <span translate="no"
              className={
                "font-display text-3xl leading-none tabular-nums " +
                (low ? "text-amber-700" : "text-slate-900")
              }
            >
              <span>{ceiling.remaining.toLocaleString()}</span>
              <span className="text-lg text-slate-400">
                <span>{" / "}
                <span>{ceiling.limit.toLocaleString()}</span></span>
              </span>
            </span>
          )}
          <p
            translate="no"
            className="mt-1.5 min-h-4 text-xs text-slate-400"
            title={
              ceiling?.upkeep
                ? `Requests your drafts, publishing and Replace images made today, in all your shops. Plus ${ceiling.upkeep.toLocaleString()} keeping your shops and profiles current, which don't count toward your limit. Resets at 00:00 UTC.`
                : "Requests your drafts, publishing and Replace images made today, in all your shops. Keeping your shops and profiles current doesn't count. Resets at 00:00 UTC."
            }
          >
            {ceiling === null
              ? ""
              : `${ceiling.used.toLocaleString()} used · resets ${ceiling.resets_label}` +
                (average != null ? ` · avg ${average.toLocaleString()}/day over 7 days` : "")}
          </p>
        </div>
        {quota && <UsageChart key="usagechart-229-8" history={quota.history} />}
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
