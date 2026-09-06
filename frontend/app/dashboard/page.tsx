"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, BatchSummary, Connection, Quota, ShopListing } from "@/lib/types";
import { etsyListingLink, relativeTime } from "@/lib/format";
import { StatusPill } from "@/components/StatusPill";

/** Thumbnails for the activity timeline, loaded after the page paints. */
type Thumbs = Record<string, Asset[]>;

const RECENT = 5;

export default function Dashboard() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [listings, setListings] = useState<ShopListing[] | null>(null);
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [thumbs, setThumbs] = useState<Thumbs>({});

  useEffect(() => {
    let cancelled = false;
    const set = <T,>(f: (v: T) => void) => (v: T) => {
      if (!cancelled) f(v);
    };

    api.connection().then(set(setConnection)).catch(() => setConnection(null));
    api.quota().then(set(setQuota)).catch(() => {});
    api
      .shopListings()
      .then(set<{ listings: ShopListing[] }>((r) => setListings(r.listings)))
      .catch(() => setListings([]));

    api
      .listBatches()
      .then(async (list) => {
        if (cancelled) return;
        setBatches(list);
        // One thumbnail row per recent batch; failures just leave that row empty.
        const recent = list.slice(0, RECENT);
        const results = await Promise.allSettled(recent.map((b) => api.getBatch(b.id)));
        if (cancelled) return;
        const next: Thumbs = {};
        results.forEach((r, i) => {
          if (r.status === "fulfilled") {
            next[recent[i].id] = r.value.assets.filter((a) => a.status === "processed");
          }
        });
        setThumbs(next);
      })
      .catch(() => setBatches([]));

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">
            Your shop, your remaining API quota, and what you have been working on.
          </p>
        </div>
        <Link href="/upload" className="btn-primary shrink-0">
          New upload
        </Link>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ShopCard connection={connection} listings={listings} />
        <QuotaCard quota={quota} />
      </div>

      <ActivityCard batches={batches} thumbs={thumbs} />
    </div>
  );
}

/* ---------------------------------------------------------------- shop ---- */

function ShopCard({
  connection,
  listings,
}: {
  connection: Connection | null;
  listings: ShopListing[] | null;
}) {
  if (connection && !connection.connected) {
    return (
      <section className="card flex flex-col items-start gap-3 p-6 lg:col-span-2">
        <h2 className="label mb-0">Shop</h2>
        <p className="text-sm text-slate-500">
          No Etsy shop is connected yet. Connect one to load your listings and profiles.
        </p>
        <Link href="/connect" className="btn-secondary">
          Connect a shop
        </Link>
      </section>
    );
  }

  const name = connection?.shop_name ?? null;
  const active = listings?.filter((l) => l.state === "active").length ?? 0;
  const drafts = listings?.filter((l) => l.state === "draft").length ?? 0;
  const tiles = (listings ?? []).filter((l) => l.thumbnail_url).slice(0, 8);

  return (
    <section className="card p-6 lg:col-span-2">
      <div className="flex items-start gap-4">
        <Avatar name={name} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {connection === null ? (
              <span className="h-5 w-40 animate-pulse rounded bg-slate-100" />
            ) : (
              <h2 className="truncate text-lg font-semibold text-slate-900">
                {name ?? "Your shop"}
              </h2>
            )}
            {connection?.connected && <StatusPill status="ready" />}
          </div>
          {connection?.connected_at && (
            <p className="mt-1 text-xs text-slate-500">
              Connected{" "}
              <span title={new Date(connection.connected_at).toLocaleString()}>
                {relativeTime(connection.connected_at)}
              </span>
            </p>
          )}
          <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2">
            <Metric label="Listings" value={listings?.length ?? null} />
            <Metric label="Active" value={listings === null ? null : active} />
            <Metric label="Drafts" value={listings === null ? null : drafts} />
          </dl>
        </div>
      </div>

      {listings === null ? (
        <div className="mt-5 flex gap-1.5">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-16 w-16 animate-pulse rounded-lg bg-slate-100" />
          ))}
        </div>
      ) : tiles.length > 0 ? (
        <div className="mt-5">
          <p className="label">Your listings</p>
          <div className="flex flex-wrap gap-1.5">
            {tiles.map((l) => (
              <a
                key={l.listing_id}
                href={etsyListingLink(l.listing_id, l.state, l.url)}
                target="_blank"
                rel="noopener noreferrer"
                title={
                  (l.title ?? String(l.listing_id)) +
                  (l.state === "active" ? " — view on Etsy" : " — edit draft in Shop Manager")
                }
                className="h-16 w-16 overflow-hidden rounded-lg border border-slate-200 bg-slate-50 transition-colors hover:border-brand-600"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={l.thumbnail_url ?? ""}
                  alt={l.title ?? ""}
                  className="h-full w-full object-cover"
                  loading="lazy"
                  decoding="async"
                />
              </a>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

/** Monogram tile — the Etsy API response carries no shop avatar image. */
function Avatar({ name }: { name: string | null }) {
  const initials = (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  return (
    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-brand-100 bg-brand-50 text-base font-semibold text-brand-700">
      {initials || "—"}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums text-slate-900">
        {value === null ? <span className="inline-block h-5 w-8 animate-pulse rounded bg-slate-100" /> : value}
      </dd>
    </div>
  );
}

/* --------------------------------------------------------------- quota ---- */

function QuotaCard({ quota }: { quota: Quota | null }) {
  if (!quota) {
    return (
      <section className="card space-y-3 p-6">
        <div className="h-3 w-24 animate-pulse rounded bg-slate-100" />
        <div className="h-8 w-20 animate-pulse rounded bg-slate-100" />
        <div className="h-1.5 w-full animate-pulse rounded-full bg-slate-100" />
      </section>
    );
  }
  const pct = (u: number, l: number) => (l > 0 ? Math.min(100, (u / l) * 100) : 0);
  const low = quota.tenant_remaining < quota.tenant_limit * 0.1;

  return (
    <section className="card p-6">
      <div className="flex items-baseline justify-between">
        <h2 className="label mb-0">Etsy API quota</h2>
        <span className="text-xs tabular-nums text-slate-400">{quota.usage_date}</span>
      </div>

      <p className="mt-3 flex items-baseline gap-1.5">
        <span
          className={
            "font-display text-3xl font-normal tabular-nums " +
            (low ? "text-amber-700" : "text-slate-900")
          }
        >
          {quota.tenant_remaining.toLocaleString()}
        </span>
        <span className="text-sm text-slate-500">left today</span>
      </p>

      <div className="mt-4 space-y-3">
        <Meter
          label="This shop"
          used={quota.tenant_used}
          limit={quota.tenant_limit}
          pct={pct(quota.tenant_used, quota.tenant_limit)}
          tone={low ? "bg-amber-600" : "bg-brand-600"}
        />
        <Meter
          label="App-wide"
          used={quota.global_used}
          limit={quota.global_limit}
          pct={pct(quota.global_used, quota.global_limit)}
          tone="bg-slate-400"
        />
      </div>

      <p className="mt-4 text-xs text-slate-400">Resets at 00:00 UTC.</p>
    </section>
  );
}

function Meter({
  label,
  used,
  limit,
  pct,
  tone,
}: {
  label: string;
  used: number;
  limit: number;
  pct: number;
  tone: string;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-xs">
        <span className="text-slate-500">{label}</span>
        <span className="tabular-nums text-slate-500">
          <span className="font-medium text-slate-700">{used.toLocaleString()}</span> /{" "}
          {limit.toLocaleString()}
        </span>
      </div>
      <div className="progress">
        <div className={"h-full rounded-full transition-all " + tone} style={{ width: pct + "%" }} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ activity ---- */

function ActivityCard({ batches, thumbs }: { batches: BatchSummary[] | null; thumbs: Thumbs }) {
  return (
    <section className="card p-6">
      <div className="flex items-baseline justify-between">
        <h2 className="label mb-0">Recent activity</h2>
        <Link href="/" className="text-xs font-medium text-brand-700 hover:text-brand-800">
          All batches
        </Link>
      </div>

      {batches === null && (
        <div className="mt-4 space-y-4">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-center gap-4">
              <div className="h-12 w-12 animate-pulse rounded-lg bg-slate-100" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-40 animate-pulse rounded bg-slate-100" />
                <div className="h-3 w-24 animate-pulse rounded bg-slate-100" />
              </div>
            </div>
          ))}
        </div>
      )}

      {batches?.length === 0 && (
        <p className="mt-4 text-sm text-slate-500">
          Nothing uploaded yet. Your batches will appear here.
        </p>
      )}

      {batches && batches.length > 0 && (
        <ol className="mt-4">
          {batches.slice(0, RECENT).map((b, i, arr) => (
            <ActivityRow
              key={b.id}
              batch={b}
              assets={thumbs[b.id]}
              last={i === arr.length - 1}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function ActivityRow({
  batch,
  assets,
  last,
}: {
  batch: BatchSummary;
  assets?: Asset[];
  last: boolean;
}) {
  // One thumbnail per listing group, same rule as the batches list.
  const byGroup = new Map<string, Asset>();
  for (const a of assets ?? []) {
    const key = a.group_key ?? "";
    const seen = byGroup.get(key);
    if (!seen || (a.rank ?? 99) < (seen.rank ?? 99)) byGroup.set(key, a);
  }
  const tiles = [...byGroup.values()].slice(0, 3);
  const sku = (assets ?? []).find((a) => a.parsed_sku)?.parsed_sku ?? null;
  const groups = byGroup.size;
  const title = sku
    ? groups > 1
      ? sku + " + " + (groups - 1) + " more"
      : sku
    : "Batch " + batch.id.slice(0, 8);

  return (
    <li className="relative flex gap-4 pb-5 last:pb-0">
      {/* Timeline rail */}
      {!last && <span aria-hidden className="absolute left-[5px] top-4 h-full w-px bg-slate-200" />}
      <span
        aria-hidden
        className="relative z-[1] mt-[7px] h-2.5 w-2.5 shrink-0 rounded-full border-2 border-white bg-slate-300 ring-1 ring-slate-200"
      />

      <Link
        href={"/batches/" + batch.id}
        className="group -my-1 flex min-w-0 flex-1 items-center gap-4 rounded-lg px-2 py-1 transition-colors hover:bg-slate-50"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium text-slate-900 group-hover:text-brand-700">
              {title}
            </span>
            <StatusPill status={batch.status} />
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            <span className="tabular-nums">{batch.asset_count}</span> files ·{" "}
            <span title={new Date(batch.created_at).toLocaleString()}>
              {relativeTime(batch.created_at)}
            </span>
          </p>
        </div>

        {assets === undefined ? (
          <div className="hidden gap-1.5 sm:flex">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-12 w-12 animate-pulse rounded-lg bg-slate-100" />
            ))}
          </div>
        ) : (
          tiles.length > 0 && (
            <div className="hidden shrink-0 gap-1.5 sm:flex">
              {tiles.map((a) => (
                <div
                  key={a.id}
                  className="h-12 w-12 overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.assetImage(a.id, 112)}
                    alt={a.original_filename}
                    className="h-full w-full object-cover"
                    loading="lazy"
                    decoding="async"
                  />
                </div>
              ))}
            </div>
          )
        )}
      </Link>
    </li>
  );
}
