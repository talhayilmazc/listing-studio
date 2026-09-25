"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, BatchSummary, Content, Group, Profile } from "@/lib/types";
import { StatusPill } from "@/components/StatusPill";
import { relativeTime } from "@/lib/format";
import { BatchActions } from "@/components/BatchActions";

/** Per-batch detail loaded after the list paints, so the page never waits on it. */
interface Enrichment {
  assets: Asset[];
  groups: Group[];
  content: Content[];
}

export default function Home() {
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [extra, setExtra] = useState<Record<string, Enrichment>>({});
  const [profiles, setProfiles] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  // Batches ticked for a bulk action (create drafts / publish).
  const [selected, setSelected] = useState<string[]>([]);
  // Bumped after a bulk action, so the cards reload their progress.
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    api
      .listBatches()
      .then(async (list) => {
        if (cancelled) return;
        setBatches(list);

        // Profile names for the chips; cards render fine without them.
        api
          .listProfiles()
          .then((ps: Profile[]) => {
            if (!cancelled) setProfiles(Object.fromEntries(ps.map((p) => [p.id, p.name])));
          })
          .catch(() => {});

        // Enrich a few batches at a time. Each card fills in as its data lands;
        // a failure just leaves that card in its summary-only form.
        const queue = [...list];
        const worker = async () => {
          while (queue.length && !cancelled) {
            const b = queue.shift();
            if (!b) return;
            const [d, g, c] = await Promise.allSettled([
              api.getBatch(b.id),
              api.listGroups(b.id),
              api.listContent(b.id),
            ]);
            if (cancelled) return;
            setExtra((cur) => ({
              ...cur,
              [b.id]: {
                assets: d.status === "fulfilled" ? d.value.assets : [],
                groups: g.status === "fulfilled" ? g.value : [],
                content: c.status === "fulfilled" ? c.value : [],
              },
            }));
          }
        };
        await Promise.all([worker(), worker(), worker()]);
      })
      .catch((e) => setError(String(e.message ?? e)));

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  return (
    <div className="space-y-6">
      {error && (
        <div className="card border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div>
      )}

      {batches === null && !error && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <BatchSkeleton key={i} />
          ))}
        </div>
      )}

      {batches && batches.length === 0 && (
        <div className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No batches yet.</p>
          <Link href="/upload" className="btn-primary">
            Upload your first folder
          </Link>
        </div>
      )}

      {batches && batches.length > 0 && (
        <>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300"
                checked={selected.length === batches.length}
                ref={(el) => {
                  if (el) el.indeterminate = selected.length > 0 && selected.length < batches.length;
                }}
                onChange={(e) => setSelected(e.target.checked ? batches.map((b) => b.id) : [])}
              />
              Select all
            </label>
            <span>Tick batches to create drafts or publish without opening each one.</span>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {batches.map((b) => (
              <div key={b.id} className="relative">
                <BatchCard batch={b} extra={extra[b.id]} profiles={profiles} selected={selected.includes(b.id)} />
                {/* Outside the card's link, so ticking never opens the batch. */}
                <label
                  className="absolute left-3 top-3 z-10 flex h-8 w-8 cursor-pointer items-center justify-center rounded-md bg-white/90 shadow-sm ring-1 ring-slate-200"
                  title="Select for a bulk action"
                >
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-slate-300"
                    checked={selected.includes(b.id)}
                    onChange={() => toggle(b.id)}
                    aria-label={`Select batch ${b.id.slice(0, 8)}`}
                  />
                </label>
              </div>
            ))}
          </div>
          <BatchActions
            selected={selected}
            onClear={() => setSelected([])}
            onDone={() => {
              setSelected([]);
              setReloadKey((k) => k + 1);
            }}
          />
        </>
      )}
    </div>
  );
}

function BatchCard({
  batch,
  extra,
  profiles,
  selected = false,
}: {
  batch: BatchSummary;
  extra?: Enrichment;
  profiles: Record<string, string>;
  selected?: boolean;
}) {
  const assets = extra?.assets ?? [];
  const groups = extra?.groups ?? [];
  const content = extra?.content ?? [];

  // One image per listing group (its rank-1 image), so the mosaic shows distinct
  // designs rather than several angles of the same one.
  const usable = assets.filter((a) => a.status === "processed");
  const byGroup = new Map<string, Asset>();
  for (const a of usable) {
    const key = a.group_key ?? "";
    const seen = byGroup.get(key);
    if (!seen || (a.rank ?? 99) < (seen.rank ?? 99)) byGroup.set(key, a);
  }
  const tiles = [...byGroup.values()].slice(0, 5);
  const listings = groups.length || byGroup.size;
  const more = Math.max(0, (listings || usable.length) - tiles.length);

  // "AD3 + 32 more" reads better than an opaque hash; fall back to the id.
  const skus = groups.map((g) => g.sku).filter((s): s is string => !!s);
  const firstSku = skus[0] ?? usable.find((a) => a.parsed_sku)?.parsed_sku ?? null;
  const title = firstSku
    ? listings > 1
      ? firstSku + " + " + (listings - 1) + " more"
      : firstSku
    : "Batch " + batch.id.slice(0, 8);

  const profileNames = [
    ...new Set(
      groups
        .map((g) => (g.profile_id ? profiles[g.profile_id] : null))
        .filter((n): n is string => !!n),
    ),
  ];

  // Funnel: published within drafted within approved within generated
  // (creating a draft requires approval).
  const generated = content.length;
  const approved = content.filter((c) => c.approved).length;
  // Per listing, in any shop: drafted somewhere, live somewhere (v5 §E).
  const drafted = content.filter((c) => c.publications.length > 0).length;
  const published = content.filter((c) => c.publications.some((p) => p.state === "active")).length;

  return (
    <Link
      href={"/batches/" + batch.id}
      className={
        "card group flex h-full flex-col overflow-hidden transition-colors hover:border-brand-600 " +
        (selected ? "border-brand-600 ring-2 ring-brand-500/30" : "")
      }
    >
      <Mosaic tiles={tiles} more={more} pending={!extra} />

      <div className="flex flex-1 flex-col p-5">
        <div className="flex items-start justify-between gap-3">
          <h2 className="min-w-0 flex-1 truncate font-display text-lg leading-tight text-slate-900">
            {title}
          </h2>
          <StatusPill status={batch.status} />
        </div>

        {profileNames.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {profileNames.map((n) => (
              <span
                key={n}
                className="rounded-md border border-brand-100 bg-brand-50 px-1.5 py-0.5 text-xs font-medium text-brand-700"
              >
                {n}
              </span>
            ))}
          </div>
        )}

        <div className="mt-3 flex-1">
          <Progress
            batch={batch}
            listings={listings}
            generated={generated}
            approved={approved}
            drafted={drafted}
            published={published}
            pending={!extra}
          />
        </div>

        <div className="mt-4 flex items-baseline justify-between gap-3 text-xs text-slate-400">
          <span>
            <span className="tabular-nums">{listings || batch.asset_count}</span>{" "}
            {listings === 1 ? "listing" : "listings"} ·{" "}
            <span className="tabular-nums">{batch.asset_count}</span> files ·{" "}
            <span title={new Date(batch.created_at).toLocaleString()}>
              {relativeTime(batch.created_at)}
            </span>
          </span>
          {/* Affordance inside the existing card link — not an added button. */}
          <span className="shrink-0 font-medium text-brand-700 opacity-0 transition-opacity group-hover:opacity-100">
            Open →
          </span>
        </div>
      </div>
    </Link>
  );
}

/**
 * Card imagery (docs/ui-direction-v2.md §4).
 *
 * Three or more designs get the mosaic: one dominant tile with a 2×2 beside it.
 * Fewer than that would leave the grid half empty, so one or two designs get a
 * single full-width 16:10 image instead. Every tile is cropped server-side
 * around the artwork, so nothing is sliced through the middle.
 */
function Mosaic({ tiles, more, pending }: { tiles: Asset[]; more: number; pending: boolean }) {
  if (pending && tiles.length === 0) {
    return <div className="aspect-[16/10] w-full animate-pulse bg-slate-100" />;
  }

  if (tiles.length === 0) {
    return (
      <div className="flex aspect-[16/10] w-full items-center justify-center bg-slate-100 text-slate-300">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M3 15l5-5 4 4 3-3 6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    );
  }

  // One or two designs: a single hero reads better than a mosaic with holes.
  if (tiles.length < 3) {
    return (
      <div className="overflow-hidden bg-slate-100">
        <div className="relative aspect-[16/10] w-full transition-transform duration-200 group-hover:scale-[1.02]">
          <span className="absolute inset-0 animate-pulse bg-slate-200" aria-hidden />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api.assetImage(tiles[0].id, 896, "16:10")}
            alt={tiles[0].original_filename}
            className="relative h-full w-full object-cover"
            loading="lazy"
            decoding="async"
          />
        </div>
      </div>
    );
  }

  const cells = Array.from({ length: 5 }, (_, i) => tiles[i] ?? null);

  return (
    <div className="overflow-hidden bg-slate-100">
      <div className="aspect-[16/10] w-full transition-transform duration-200 group-hover:scale-[1.02]">
        <div className="grid h-full w-full grid-cols-4 grid-rows-2 gap-1 p-1">
          {cells.map((asset, i) => (
            <div
              key={asset?.id ?? "empty-" + i}
              className={
                "relative overflow-hidden rounded-lg bg-slate-50 " +
                (i === 0 ? "col-span-2 row-span-2" : "")
              }
            >
              {asset ? (
                <>
                  <span className="absolute inset-0 animate-pulse bg-slate-200" aria-hidden />
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.assetImage(asset.id, i === 0 ? 448 : 224, "4:5")}
                    alt={asset.original_filename}
                    className="relative h-full w-full object-cover"
                    loading="lazy"
                    decoding="async"
                  />
                  {/* Overflow count sits on the last tile rather than stealing a slot. */}
                  {more > 0 && i === cells.length - 1 && (
                    <span className="absolute inset-0 flex items-center justify-center bg-slate-900/55 text-sm font-medium tabular-nums text-white">
                      +{more}
                    </span>
                  )}
                </>
              ) : (
                <div className="h-full w-full bg-slate-100/60" />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * A single status line in the accent tone (§4). The bar appears only while work
 * is genuinely still moving — a finished batch just states where it got to.
 */
function Progress({
  batch,
  listings,
  generated,
  approved,
  drafted,
  published,
  pending,
}: {
  batch: BatchSummary;
  listings: number;
  generated: number;
  approved: number;
  drafted: number;
  published: number;
  pending: boolean;
}) {
  if (pending) {
    return <div className="h-4 w-32 animate-pulse rounded bg-slate-100" />;
  }

  const working =
    batch.status === "processing" ||
    batch.status === "uploading" ||
    (generated > 0 && listings > 0 && generated < listings);

  const [count, noun] =
    published > 0
      ? [published, "published"]
      : drafted > 0
        ? [drafted, "drafted"]
        : approved > 0
          ? [approved, "approved"]
          : generated > 0
            ? [generated, "generated"]
            : [0, ""];

  if (working) {
    const done = Math.min(generated, listings);
    const pct = listings > 0 ? (done / listings) * 100 : 0;
    return (
      <div>
        <div className="progress">
          <div className="progress-fill" style={{ width: pct + "%" }} />
        </div>
        <p className="mt-1.5 text-xs tabular-nums text-slate-500">
          {done} / {listings} generated
        </p>
      </div>
    );
  }

  if (count === 0) {
    return <p className="text-xs text-slate-400">Not generated yet</p>;
  }

  return (
    <p className="text-xs">
      <span className="font-medium tabular-nums text-brand-700">{count}</span>{" "}
      <span className="text-brand-700">{noun}</span>
      {published > 0 && drafted > published && (
        <span className="text-slate-400">
          {" · "}
          <span className="tabular-nums">{drafted - published}</span> awaiting publish
        </span>
      )}
    </p>
  );
}

function BatchSkeleton() {
  return (
    <div className="card overflow-hidden">
      <div className="aspect-[16/10] w-full animate-pulse bg-slate-100" />
      <div className="space-y-2 p-5">
        <div className="h-5 w-32 animate-pulse rounded bg-slate-100" />
        <div className="h-4 w-24 animate-pulse rounded bg-slate-100" />
        <div className="h-3 w-40 animate-pulse rounded bg-slate-100" />
      </div>
    </div>
  );
}
