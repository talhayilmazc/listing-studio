"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { relativeTime } from "@/lib/format";
import { api } from "@/lib/api";
import type { BatchSummary, Profile, ShopSummary } from "@/lib/types";
import { periodWord, useAllowance, writtenAndPublished } from "./Allowance";
import { useSession } from "./SessionProvider";
import { useShops } from "./ShopProvider";
import { supportsAll } from "@/lib/shopScope";

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
  const [shop, setShop] = useState<ShopSummary | null>(null);
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);

  const { account } = useSession();
  // Shop figures follow the shop switcher (v5 §E): "All shops" adds every shop up on the
  // pages that show every shop; a page that works on one shop shows that shop's.
  // Batches are the account's.
  const { selected: one, scope } = useShops();
  const selected = supportsAll(pathname) ? scope : one;
  const shopId = selected?.id ?? null;
  // /admin carries its own app-wide figures. For anyone else it is an ordinary
  // 404, so it keeps the strip every unknown route has.
  const hidden =
    HIDDEN.some((re) => re.test(pathname)) || (pathname === "/admin" && Boolean(account?.is_admin));

  useEffect(() => {
    if (hidden) return;
    let cancelled = false;
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
          period={selected ? `this month · ${selected.name}` : "this month · all shops"}
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
          period={selected ? `awaiting publish · ${selected.name}` : "awaiting publish · all shops"}
          value={shop ? (shop.shop_counts_known ? shop.draft : null) : null}
          secondary={shop && shop.shop_counts_known ? `${shop.active.toLocaleString()} live` : null}
          unavailable={
            shop && !shop.shop_counts_known ? (shop.syncing ? "syncing from Etsy…" : "not read from Etsy yet") : undefined
          }
        />
        <AllowanceCell />
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
 * The plan's number: listings written this period, and how many went live in it.
 * (This cell used to show "Etsy requests today". That is a technical limit which
 * grows with photos, attributes and shops; a seller is told about it only when it
 * makes their work wait: components/PauseNotice.tsx.)
 */
function AllowanceCell() {
  const a = useAllowance();
  const low = a ? a.remaining <= Math.max(1, a.amount * 0.1) : false;
  return (
    <div className={CELL + " bg-slate-50"} data-testid="strip-allowance">
      <Label label="Listings generated" period={a ? `left ${periodWord(a.period)}` : "left"} />
      <dd className="mt-1.5">
        {a === null ? (
          <span className="inline-block h-8 w-28 animate-pulse rounded bg-slate-100" />
        ) : (
          <span translate="no" className={"font-display text-3xl leading-none tabular-nums " + (low ? "text-amber-700" : "text-slate-900")}>
            <span>{a.remaining.toLocaleString()}</span>
            <span className="text-lg text-slate-400">
              <span>{" / "}
              <span>{a.amount.toLocaleString()}</span></span>
            </span>
          </span>
        )}
        <p translate="no" className="mt-1.5 min-h-4 text-xs text-slate-400"
          title="A listing is one design written by AI or by you. Rewriting the same design is free up to 3 times; photos and the number of drafts in your shops do not count.">
          {a === null ? "" : writtenAndPublished(a)}
        </p>
      </dd>
    </div>
  );
}
