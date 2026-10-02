"use client";

import { ACTION_LABEL, money } from "@/lib/analytics";
import type { Action, ActionKind, AnalyticsSummary } from "@/lib/types";
import { ListingCell } from "./Shared";

const KIND_STYLE: Record<ActionKind, string> = {
  ad_sink: "bg-rose-50 text-rose-800 ring-rose-200",
  ads_above_break_even: "bg-rose-50 text-rose-800 ring-rose-200",
  selling_at_loss: "bg-rose-50 text-rose-800 ring-rose-200",
  fading: "bg-amber-50 text-amber-800 ring-amber-200",
  turned_down: "bg-amber-50 text-amber-800 ring-amber-200",
  room_to_advertise: "bg-emerald-50 text-emerald-800 ring-emerald-200",
};

export function ActionKindBadge({ kind }: { kind: ActionKind }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${KIND_STYLE[kind]}`}>
      {ACTION_LABEL[kind]}
    </span>
  );
}

/** One thing worth doing: why, what, and the money it is about. */
export function ActionItem({ action, rank, days, currency }: { action: Action; rank?: number; days: number; currency: string | null }) {
  const gain = action.kind === "room_to_advertise";
  return (
    <li className="flex gap-3 py-4">
      {rank !== undefined && (
        <span key="rank" translate="no" className="mt-0.5 w-5 shrink-0 text-right text-sm tabular-nums text-slate-400">{rank}</span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0 flex-1 basis-56">
            {/* On the listing's own page the heading already names it. */}
            {action.listing ? <ListingCell row={action.listing} days={days} /> : <ActionKindBadge kind={action.kind} />}
          </div>
          <div className="text-right">
            <p translate="no" className="text-lg font-semibold tabular-nums leading-tight text-slate-900">{money(action.stake, currency)}</p>
            <p className="text-[11px] text-slate-500">{gain ? "its ads net per 30 days" : "at stake per 30 days"}</p>
          </div>
        </div>
        <p className="mt-2 text-sm text-slate-700">
          {action.listing && <span key="kind" className="mr-1"><ActionKindBadge kind={action.kind} /></span>}
          <span>{action.reason}</span>
        </p>
        <p className="mt-1 text-sm font-medium text-slate-900">{action.action}</p>
        <p className="tap-row mt-1.5 flex flex-wrap gap-3 text-xs">
          {action.links.map((l) => (
            <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="tap text-brand-700 hover:underline max-sm:py-1.5">
              <span><span>{l.label}</span> ↗</span>
            </a>
          ))}
        </p>
      </div>
    </li>
  );
}

/**
 * The default view: what to do today, most money first. Each item is acted on
 * in Shop Manager; nothing on Etsy is changed from here.
 */
export function Today({ data, onListings }: { data: AnalyticsSummary; onListings: () => void }) {
  const ccy = data.data.currency ?? null;
  const days = data.period?.days ?? 30;
  const actions = data.actions ?? [];
  const total = data.actions_total ?? actions.length;
  const salesRead = data.data.sales?.from;

  if (!salesRead) {
    return (
      <div className="card p-6 text-sm text-slate-600">
        Nothing can be ranked until your sales are read (above). Once they are, this lists what to do first and the money
        each item is about.
      </div>
    );
  }
  if (actions.length === 0) {
    return (
      <div className="card p-6 text-sm text-slate-600">
        <p className="font-medium text-slate-800">Nothing needs doing today.</p>
        <p className="mt-1">
          <span>No listing in the last <span translate="no">{days}</span> days is losing money on ads, selling at a loss, or dropping against the period before.</span>
          {!data.data.reports_until && (
            <span key="noads"> Ad spend per listing isn&apos;t known without an Ads report, so ad problems can&apos;t show up here yet.</span>
          )}
        </p>
      </div>
    );
  }
  const losing = actions.filter((a) => a.kind !== "room_to_advertise").reduce((s, a) => s + a.stake, 0);
  return (
    <section className="card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-800">What to do today</h2>
        <p className="text-xs text-slate-500">
          <span translate="no" className="font-semibold tabular-nums text-slate-800">{money(losing, ccy)}</span>
          <span>{` at stake per 30 days across ${actions.length === total ? "these" : `the top ${actions.length}`}`}</span>
        </p>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        <span>Ranked by money, not by size of the listing. &quot;At stake&quot; is what the problem costs over 30 days at the current
        rate: the ad spend with nothing back, the loss, or the sales that stopped coming. It is an estimate from your own
        figures, not a forecast.</span>
        {!data.data.costs_entered?.product && (
          <span key="nocost"> Product cost isn&apos;t entered, so margins here are before the cost of the product.</span>
        )}
      </p>
      <ol className="mt-2 divide-y divide-slate-100">
        {actions.map((a, i) => (
          <ActionItem key={`${a.listing_id}-${a.kind}`} action={a} rank={i + 1} days={days} currency={ccy} />
        ))}
      </ol>
      {total > actions.length && (
        <button key="more" type="button" className="mt-2 text-sm font-medium text-brand-700 hover:underline" onClick={onListings}>
          <span>See all <span translate="no">{total.toLocaleString()}</span> in Listings, ranked the same way</span>
        </button>
      )}
    </section>
  );
}
