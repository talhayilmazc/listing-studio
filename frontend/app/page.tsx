"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, BatchSummary, Content, Group, Profile } from "@/lib/types";
import { StatusPill } from "@/components/StatusPill";
import { relativeTime } from "@/lib/format";

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
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Upload batches</h1>
          <p className="mt-1 text-sm text-slate-500">
            Upload a folder of your original designs, then review and approve the generated draft
            listings.
          </p>
        </div>
        <Link href="/upload" className="btn-primary">
          New upload
        </Link>
      </div>

      {error && (
        <div className="card border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div>
      )}

      {batches === null && !error && (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
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
        <div className="grid gap-3">
          {batches.map((b) => (
            <BatchCard key={b.id} batch={b} extra={extra[b.id]} profiles={profiles} />
          ))}
        </div>
      )}
    </div>
  );
}

function BatchCard({
  batch,
  extra,
  profiles,
}: {
  batch: BatchSummary;
  extra?: Enrichment;
  profiles: Record<string, string>;
}) {
  const assets = extra?.assets ?? [];
  const groups = extra?.groups ?? [];
  const content = extra?.content ?? [];

  // One thumbnail per listing group (its rank-1 image), so the strip shows
  // distinct designs rather than several angles of the same one.
  const usable = assets.filter((a) => a.status === "processed");
  const byGroup = new Map<string, Asset>();
  for (const a of usable) {
    const key = a.group_key ?? "";
    const seen = byGroup.get(key);
    if (!seen || (a.rank ?? 99) < (seen.rank ?? 99)) byGroup.set(key, a);
  }
  const thumbs = [...byGroup.values()].slice(0, 5);
  const listings = groups.length || byGroup.size;
  const moreThumbs = Math.max(0, (listings || usable.length) - thumbs.length);

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
  const drafted = content.filter((c) => c.etsy_listing_id !== null).length;
  const published = content.filter((c) => c.etsy_listing_state === "active").length;
  const total = Math.max(listings, generated, 1);

  return (
    <Link
      href={"/batches/" + batch.id}
      className="card group flex items-start gap-5 p-5 transition-colors hover:border-slate-300"
    >
      <ThumbStrip thumbs={thumbs} more={moreThumbs} pending={!extra} />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-900">{title}</span>
          <StatusPill status={batch.status} />
        </div>

        <p className="mt-1 text-xs text-slate-500">
          <span className="tabular-nums">{listings || batch.asset_count}</span>{" "}
          {listings === 1 ? "listing" : "listings"} ·{" "}
          <span className="tabular-nums">{batch.asset_count}</span> files ·{" "}
          <span title={new Date(batch.created_at).toLocaleString()}>
            {relativeTime(batch.created_at)}
          </span>
        </p>

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

        <FunnelBar
          total={total}
          generated={generated}
          approved={approved}
          drafted={drafted}
          published={published}
          pending={!extra}
        />
      </div>

      <span
        aria-hidden
        className="shrink-0 self-center text-slate-300 transition-colors group-hover:text-brand-600"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </Link>
  );
}

function ThumbStrip({
  thumbs,
  more,
  pending,
}: {
  thumbs: Asset[];
  more: number;
  pending: boolean;
}) {
  if (pending && thumbs.length === 0) {
    return (
      <div className="flex shrink-0 gap-1.5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-14 w-14 animate-pulse rounded-lg bg-slate-100" />
        ))}
      </div>
    );
  }
  if (thumbs.length === 0) {
    return (
      <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg border border-dashed border-slate-200 text-slate-300">
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        >
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M3 15l5-5 4 4 3-3 6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    );
  }
  return (
    <div className="flex shrink-0 gap-1.5">
      {thumbs.map((a) => (
        <div
          key={a.id}
          className="h-14 w-14 overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
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
      {more > 0 && (
        <div className="flex h-14 w-14 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-xs font-medium tabular-nums text-slate-500">
          +{more}
        </div>
      )}
    </div>
  );
}

/** The funnel as one stacked track, most-complete stage first. */
function FunnelBar({
  total,
  generated,
  approved,
  drafted,
  published,
  pending,
}: {
  total: number;
  generated: number;
  approved: number;
  drafted: number;
  published: number;
  pending: boolean;
}) {
  if (pending) {
    return <div className="mt-3 h-1.5 w-full animate-pulse rounded-full bg-slate-100" />;
  }
  const pct = (n: number) => Math.max(0, (n / total) * 100) + "%";
  const segments = [
    { key: "published", w: published, cls: "bg-emerald-600" },
    { key: "drafted", w: drafted - published, cls: "bg-brand-600" },
    { key: "approved", w: approved - drafted, cls: "bg-brand-100" },
    { key: "generated", w: generated - approved, cls: "bg-slate-300" },
  ].filter((s) => s.w > 0);

  return (
    <div className="mt-3">
      <div className="progress flex">
        {segments.map((s) => (
          <div key={s.key} className={s.cls} style={{ width: pct(s.w) }} />
        ))}
      </div>
      <p className="mt-1.5 flex flex-wrap gap-x-3 text-xs tabular-nums text-slate-500">
        <Stat n={generated} label="generated" dot="bg-slate-300" />
        <Stat n={approved} label="approved" dot="bg-brand-100" />
        <Stat n={drafted} label="drafted" dot="bg-brand-600" />
        <Stat n={published} label="published" dot="bg-emerald-600" />
      </p>
    </div>
  );
}

function Stat({ n, label, dot }: { n: number; label: string; dot: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={"h-1.5 w-1.5 rounded-full " + dot} />
      <span className="font-medium text-slate-700">{n}</span> {label}
    </span>
  );
}

function BatchSkeleton() {
  return (
    <div className="card flex items-start gap-5 p-5">
      <div className="flex shrink-0 gap-1.5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-14 w-14 animate-pulse rounded-lg bg-slate-100" />
        ))}
      </div>
      <div className="flex-1 space-y-2">
        <div className="h-4 w-40 animate-pulse rounded bg-slate-100" />
        <div className="h-3 w-56 animate-pulse rounded bg-slate-100" />
        <div className="h-1.5 w-full animate-pulse rounded-full bg-slate-100" />
      </div>
    </div>
  );
}
