"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BatchNameForm } from "@/components/BatchName";
import { ShopBadge } from "@/components/ShopPicker";
import { api } from "@/lib/api";
import type { Asset, BatchSummary, Content, Group, Profile } from "@/lib/types";
import { StatusPill } from "@/components/StatusPill";
import { relativeTime } from "@/lib/format";
import { BatchActions } from "@/components/BatchActions";
import { DeleteBatches } from "@/components/DeleteBatches";

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
  // Batches waiting for the delete confirmation (v7 §E3), and what happened.
  const [deleting, setDeleting] = useState<string[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const summaryOf = (ids: string[]) => ({
    batches: ids.length,
    files: ids.reduce((n, id) => n + (batches?.find((b) => b.id === id)?.asset_count ?? 0), 0),
    onEtsy: ids.reduce(
      (n, id) => n + (extra[id]?.content ?? []).reduce((m, c) => m + c.publications.length, 0),
      0,
    ),
  });

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

  // Finding a batch again: by the name the seller gave it, or the one taken from its contents.
  const [q, setQ] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (batches ?? []).filter((b) => {
    const hay = `${b.name} ${(b.shop_names ?? []).join(" ")}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });

  return (
    <div className="space-y-6">
      {error && (
        <div key="div-94-6" className="card border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div>
      )}

      {batches === null && !error && (
        <div key="div-98-6" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <BatchSkeleton key={i} />
          ))}
        </div>
      )}

      {notice && (
        <div key="div-106-6" role="status" translate="no" className="card flex items-start justify-between gap-3 p-3 text-sm text-slate-700">
          <span>{notice}</span>
          <button type="button" className="text-xs text-slate-400 underline" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}

      {batches && batches.length === 0 && (
        <div key="div-115-6" className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No batches yet.</p>
          <Link href="/upload" className="btn-primary">
            Upload your first folder
          </Link>
        </div>
      )}

      {batches && batches.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="search"
              className="field w-full py-1.5 sm:w-80"
              placeholder="Search batches by name"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search batches by name"
            />
            {q && (
              <span key="count" translate="no" className="text-xs text-slate-500">
                {`${shown.length} of ${batches.length}`}
              </span>
            )}
          </div>
          {shown.length === 0 && (
            <p key="nomatch" className="text-sm text-slate-500">No batch has a name like that.</p>
          )}
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
            {shown.map((b) => (
              <div key={b.id} className="relative">
                <BatchCard batch={b} extra={extra[b.id]} profiles={profiles} selected={selected.includes(b.id)} />
                {/* Naming it, outside the card's link like the other two controls. */}
                <button
                  type="button"
                  onClick={() => setRenaming(renaming === b.id ? null : b.id)}
                  aria-label={`Rename ${b.name}`}
                  title="Name this batch"
                  className="absolute right-12 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-md bg-white/90 text-slate-500 shadow-sm ring-1 ring-slate-200 hover:text-brand-700 max-sm:right-14 max-sm:top-2 max-sm:h-11 max-sm:w-11"
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                    <path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {renaming === b.id && (
                  <div key="rename" className="absolute inset-x-3 top-14 z-20 rounded-lg border border-slate-200 bg-white p-3 shadow-lg max-sm:top-16">
                    <BatchNameForm
                      batch={b}
                      onCancel={() => setRenaming(null)}
                      onDone={(updated) => {
                        setRenaming(null);
                        setBatches((cur) => (cur ? cur.map((x) => (x.id === updated.id ? { ...x, ...updated } : x)) : cur));
                      }}
                    />
                  </div>
                )}
                {/* Outside the card's link, so ticking never opens the batch. */}
                <button
                  type="button"
                  onClick={() => setDeleting([b.id])}
                  aria-label={`Delete ${b.name}`}
                  title="Delete this batch (nothing on Etsy is touched)"
                  className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-md bg-white/90 text-slate-500 shadow-sm ring-1 ring-slate-200 hover:text-rose-700 max-sm:right-2 max-sm:top-2 max-sm:h-11 max-sm:w-11"
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                    <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                <label
                  className="absolute left-3 top-3 z-10 flex h-8 w-8 cursor-pointer items-center justify-center rounded-md bg-white/90 shadow-sm ring-1 ring-slate-200 max-sm:left-2 max-sm:top-2 max-sm:h-11 max-sm:w-11"
                  title="Select for a bulk action"
                >
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-slate-300"
                    checked={selected.includes(b.id)}
                    onChange={() => toggle(b.id)}
                    aria-label={`Select ${b.name}`}
                  />
                </label>
              </div>
            ))}
          </div>
          {deleting && (
            <div key="div-172-10" className="sticky bottom-4 z-30">
              <DeleteBatches
                ids={deleting}
                summary={summaryOf(deleting)}
                onClose={() => setDeleting(null)}
                onDeleted={(message) => {
                  setDeleting(null);
                  setSelected((cur) => cur.filter((id) => !deleting.includes(id)));
                  setNotice(message);
                  setReloadKey((k) => k + 1);
                }}
              />
            </div>
          )}
          <BatchActions
            selected={selected}
            onClear={() => setSelected([])}
            onDone={() => {
              setSelected([]);
              setReloadKey((k) => k + 1);
            }}
            onDelete={() => setDeleting(selected)}
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
          <h2 translate="no" className="min-w-0 flex-1 truncate font-display text-lg leading-tight text-slate-900" title={batch.name}>
            {batch.name || title}
          </h2>
          <StatusPill status={batch.status} />
        </div>

        {(batch.shop_names ?? []).length > 0 && (
          <div key="shops" className="mt-2 flex flex-wrap gap-1.5">
            {(batch.shop_names ?? []).map((n) => (
              <ShopBadge key={n} name={n} />
            ))}
          </div>
        )}
        {profileNames.length > 0 && (
          <div key="div-273-8" className="mt-2 flex flex-wrap gap-1.5">
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
            <span translate="no" className="tabular-nums">{listings || batch.asset_count}</span><span>{" "}
            <span>{listings === 1 ? "listing" : "listings"}</span> ·{" "}</span>
            <span translate="no" className="tabular-nums">{batch.asset_count}</span> files ·{" "}
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
                    <span translate="no" key="span-386-18" className="absolute inset-0 flex items-center justify-center bg-slate-900/55 text-sm font-medium tabular-nums text-white">
                      <span>+<span>{more}</span></span>
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
        <p translate="no" className="mt-1.5 text-xs tabular-nums text-slate-500">
          <span><span>{done}</span> / <span>{listings}</span> generated</span>
        </p>
      </div>
    );
  }

  if (count === 0) {
    return <p className="text-xs text-slate-400">Not generated yet</p>;
  }

  return (
    <p className="text-xs">
      <span translate="no" className="font-medium tabular-nums text-brand-700">{count}</span>{" "}
      <span className="text-brand-700">{noun}</span>
      {published > 0 && drafted > published && (
        <span key="span-467-6" className="text-slate-400">
          {" · "}
          <span translate="no" className="tabular-nums">{drafted - published}</span> awaiting publish
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
