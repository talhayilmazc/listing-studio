"use client";

import Link from "next/link";
import { AUTH_START_URL } from "@/lib/api";
import { CLASS_LABEL, CLASS_STYLE, changeLabel, changeTone } from "@/lib/analytics";
import { relativeTime } from "@/lib/format";
import type { AnalyticsStatus, ListingClass } from "@/lib/types";

export const PERIODS = [7, 30, 90] as const;

export function ClassBadge({ klass }: { klass: ListingClass }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${CLASS_STYLE[klass]}`}>
      {CLASS_LABEL[klass]}
    </span>
  );
}

/** A change against the previous period: the sign carries it, colour only supports it. */
export function Delta({ change, current, upIsGood = true }: { change: number | null; current: number; upIsGood?: boolean }) {
  const tone = changeTone(change, upIsGood);
  const cls = tone === "up" ? "text-emerald-700" : tone === "down" ? "text-rose-700" : "text-slate-500";
  return <span className={`tabular-nums ${cls}`}>{changeLabel(change, current)}</span>;
}

export function PeriodPicker({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-slate-300 bg-white p-0.5" role="group" aria-label="Period">
      {PERIODS.map((d) => (
        <button
          key={d}
          type="button"
          onClick={() => onChange(d)}
          aria-pressed={days === d}
          className={
            "rounded-md px-3 py-1 text-sm " +
            (days === d ? "bg-brand-600 font-medium text-white" : "text-slate-600 hover:bg-slate-100")
          }
        >
          Last {d} days
        </button>
      ))}
    </div>
  );
}

/**
 * Where the figures come from and how fresh they are: the reconnect prompt when
 * the shop hasn't allowed reading sales, when they were last read, and the button
 * to read them now.
 */
export function StatusBar({
  status,
  reading,
  onRead,
}: {
  status: AnalyticsStatus;
  reading: boolean;
  onRead: () => void;
}) {
  if (!status.connected) {
    return (
      <div className="card p-4 text-sm text-slate-600">
        Connect your Etsy shop to see its sales and profit.{" "}
        <Link href="/connect" className="font-medium text-brand-700 underline">
          Connect a shop
        </Link>
      </div>
    );
  }
  if (!status.can_read_sales) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <p>
          Reconnect this shop once to let the Service read its sales. Only which listing sold, how many,
          the price and the date are read, and they are kept as daily totals per listing for 13 months;
          nothing about your buyers is ever stored.
        </p>
        <a href={AUTH_START_URL} className="mt-2 inline-block font-medium underline">
          Reconnect
        </a>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
      {reading ? (
        <span className="text-brand-700">Reading your sales from Etsy… the figures update when it finishes.</span>
      ) : status.synced_at ? (
        <span title={new Date(status.synced_at).toLocaleString()}>
          Sales read {relativeTime(status.synced_at)} · read again every night
        </span>
      ) : (
        <span>Your sales haven&apos;t been read yet; the first read covers the last 13 months.</span>
      )}
      <button type="button" className="underline hover:text-slate-900 disabled:opacity-50" onClick={onRead} disabled={reading}>
        {status.synced_at ? "Read now" : "Read sales now"}
      </button>
      {status.titles_refreshing && (
        <span>· Listing titles are being refreshed from Etsy; until then listings show by number.</span>
      )}
    </div>
  );
}

/** A listing's thumbnail and name, linking to its analytics, with the Etsy back link. */
export function ListingCell({
  row,
  days,
}: {
  row: { listing_id: number; title: string | null; url: string; thumbnail_url: string | null; sku?: string | null };
  days: number;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      {row.thumbnail_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={row.thumbnail_url} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
      ) : (
        <span className="h-9 w-9 shrink-0 rounded bg-slate-100" aria-hidden />
      )}
      <div className="min-w-0">
        <Link
          href={`/analytics/${row.listing_id}?days=${days}`}
          className="block truncate text-slate-800 hover:text-brand-700 hover:underline"
          title={row.title ?? undefined}
        >
          {row.title ?? `Listing ${row.listing_id}`}
        </Link>
        <span className="block truncate text-xs text-slate-400">
          {row.sku ? `${row.sku} · ` : ""}
          <a href={row.url} target="_blank" rel="noreferrer" className="hover:text-brand-700 hover:underline">
            View on Etsy ↗
          </a>
        </span>
      </div>
    </div>
  );
}
