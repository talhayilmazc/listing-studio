"use client";

import { useState } from "react";
import { money } from "@/lib/analytics";
import { BASIS, LINES, SECTIONS, SOURCES, TOTALS, refundsParts, type Definition } from "@/lib/analyticsDefinitions";
import { ADS_LAG } from "@/lib/importSteps";
import type { Basis, MonthFigure, MonthSheet, MonthView } from "@/lib/types";

import { Txt } from "@/components/Txt";

/**
 * One month of the shop's money, laid out like a receipt, with last month and
 * the same month last year beside it; what needs attention first; and what
 * could not be tied to a listing. Every number says whether it is exact,
 * calculated or estimated, and a number with nothing behind it is a blank with
 * its reason, never a zero. Definitions are lib/analyticsDefinitions.ts.
 */

export const monthName = (iso: string, short = false) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", month: short ? "short" : "long", year: "numeric" });

const BASIS_STYLE: Record<Basis, string> = {
  exact: "border-slate-300 text-slate-600",
  calculated: "border-sky-300 text-sky-800",
  estimated: "border-dashed border-amber-400 text-amber-800",
};
const BASIS_SHORT: Record<Basis, string> = { exact: "exact", calculated: "calc.", estimated: "est." };

/** How a number was arrived at. On every number. */
export function BasisTag({ basis }: { basis: Basis | null | undefined }) {
  if (!basis) return null;
  return (
    <span className={`inline-block whitespace-nowrap rounded border px-1 text-[10px] leading-4 ${BASIS_STYLE[basis]}`} title={`${BASIS[basis].label}: ${BASIS[basis].text}`}>
      {BASIS_SHORT[basis]}
    </span>
  );
}

/** A label whose definition opens on a tap (and shows on hover): nothing is hover-only. */
export function Term({ def, note, className = "" }: { def: Definition; note?: string | null; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className={`min-w-0 ${className}`}>
      <button
        type="button"
        className="tap text-left underline decoration-slate-300 decoration-dotted underline-offset-4 hover:decoration-slate-500"
        title={def.text}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {def.label}
      </button>
      {open && (
        <span key="definition" className="mt-1 block max-w-prose text-xs font-normal leading-relaxed text-slate-500">
          <span>{def.text}</span>
          <Txt>{note ? ` ${note}` : ""}</Txt>
        </span>
      )}
    </span>
  );
}

function Value({ figure, currency, strong }: { figure: MonthFigure | null | undefined; currency: string | null; strong?: boolean }) {
  if (!figure || figure.minor === null) {
    return (
      <span className="text-slate-400" title={figure?.note ?? "Nothing for this month"}>
        —
      </span>
    );
  }
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      <span translate="no" className={`tabular-nums ${strong ? "font-semibold text-slate-900" : ""} ${figure.minor < 0 && !strong ? "text-slate-700" : ""}`}>
        {money(figure.minor, currency)}
      </span>
      <BasisTag basis={figure.basis} />
    </span>
  );
}

function find(sheet: MonthSheet | null | undefined, key: string): MonthFigure | null {
  if (!sheet) return null;
  if (key === "net_etsy") return sheet.net_etsy;
  if (key === "profit") return sheet.profit;
  for (const s of sheet.sections) {
    if (s.total.key === key) return s.total;
    const line = s.lines.find((l) => l.key === key);
    if (line) return line;
  }
  return null;
}

function Row({
  def,
  figure,
  view,
  strong,
  extra,
}: {
  def: Definition;
  figure: MonthFigure;
  view: MonthView;
  strong?: boolean;
  extra?: string | null;
}) {
  const before = find(view.last_month, figure.key);
  const year = find(view.last_year, figure.key);
  const blank = figure.minor === null;
  return (
    <div className={`grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-1.5 sm:grid-cols-[minmax(0,1fr)_9.5rem_9.5rem_9.5rem] ${strong ? "border-t border-slate-200 pt-2 font-medium text-slate-900" : "text-slate-700"}`}>
      <div className="min-w-0">
        <Term def={def} note={extra} />
        {blank && figure.note && <p key="why" className="mt-0.5 text-xs font-normal text-slate-500">{figure.note}</p>}
        {!blank && figure.note && <p key="note" className="mt-0.5 text-xs font-normal text-slate-500">{figure.note}</p>}
      </div>
      <div className="text-right">
        <Value figure={figure} currency={view.currency} strong={strong} />
      </div>
      {/* Phone: the two comparisons on a line of their own. */}
      <div className="col-span-2 flex flex-wrap gap-x-4 text-xs font-normal text-slate-500 sm:hidden">
        <span className="inline-flex items-center gap-1.5">
          <span>Last month</span>
          <Value figure={before} currency={view.currency} />
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span>A year ago</span>
          <Value figure={year} currency={view.currency} />
        </span>
      </div>
      <div className="hidden text-right font-normal text-slate-500 sm:block">
        <Value figure={before} currency={view.currency} />
      </div>
      <div className="hidden text-right font-normal text-slate-500 sm:block">
        <Value figure={year} currency={view.currency} />
      </div>
    </div>
  );
}

const GROUP_LABEL: Record<string, string> = {
  revenue: "Sales of orders not in the sales read yet",
  refunds: "Refunds on orders not in the sales read",
  fees: "Etsy fees with no order or listing on them",
  offsite_ads: "Offsite Ads fees on orders not in the sales read",
  labels: "Shipping labels",
  other: "Other marketing and rows not classified",
};

export function Month({ view, onCosts, onImport }: { view: MonthView; onCosts: () => void; onImport: () => void }) {
  const sheet = view.sheet;
  const c = view.currency;
  const name = monthName(view.month);
  const headline = sheet.profit.minor !== null ? sheet.profit : sheet.net_etsy;
  const headlineDef = sheet.profit.minor !== null ? TOTALS.profit : TOTALS.net_etsy;
  const charged = find(sheet, "etsy_ads");
  const be = sheet.break_even;
  const refunds = find(sheet, "refunds");
  const left = view.unattributed;
  const groups = Object.entries(left.groups ?? {});

  return (
    <div className="space-y-5">
      {sheet.incomplete.length > 0 && (
        <div key="incomplete" role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-medium">
            <span>{`${name} is incomplete`}</span>
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13px]">
            {sheet.incomplete.map((i) => (
              <li key={i.key}>{i.text}</li>
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap gap-2">
            {sheet.incomplete.some((i) => i.key === "product_cost") && (
              <button key="costs" type="button" className="btn-secondary py-1 text-xs" onClick={onCosts}>
                Enter product costs
              </button>
            )}
            {sheet.incomplete.some((i) => i.key === "no_statement") && (
              <button key="import" type="button" className="btn-secondary py-1 text-xs" onClick={onImport}>
                Import the statement
              </button>
            )}
          </div>
        </div>
      )}

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="card p-4">
          <dt className="text-xs text-slate-500"><Term def={SECTIONS.revenue} /></dt>
          <dd className="mt-1 text-2xl text-slate-900"><Value figure={find(sheet, "revenue_total") ?? sheet.revenue} currency={c} strong /></dd>
          <dd className="mt-1 text-xs text-slate-500" title={SOURCES[sheet.source].text}>{SOURCES[sheet.source].label}</dd>
        </div>
        <div className="card p-4">
          <dt className="text-xs text-slate-500"><Term def={headlineDef} /></dt>
          <dd className="mt-1 text-2xl text-slate-900"><Value figure={headline} currency={c} strong /></dd>
          <dd className="mt-1 text-xs text-slate-500">
            <Txt>{sheet.profit.minor === null && sheet.net_etsy.minor !== null ? "Product cost is not fully entered" : (headline.note ?? "")}</Txt>
          </dd>
        </div>
        <div className="card p-4">
          <dt className="text-xs text-slate-500"><Term def={TOTALS.break_even_roas} note={be.note} /></dt>
          <dd className="mt-1 flex items-center gap-1.5 text-2xl text-slate-900">
            <span translate="no" className="font-semibold tabular-nums">{be.roas !== null ? be.roas.toFixed(2) : "—"}</span>
            <BasisTag basis={be.basis} />
          </dd>
          <dd className="mt-1 text-xs text-slate-500">
            <span>
              {be.actual_roas !== null
                ? `Your Etsy Ads ROAS: ${be.actual_roas.toFixed(2)}${be.roas !== null ? (be.actual_roas >= be.roas ? " (above break-even)" : " (below break-even)") : ""}`
                : (be.actual_note ?? be.note ?? "")}
            </span>
          </dd>
        </div>
      </dl>

      <section className="card p-4 sm:p-5" aria-labelledby="attention-title">
        <h2 id="attention-title" className="font-medium text-slate-900">What needs attention</h2>
        {view.attention.length === 0 ? (
          <p key="none" className="mt-2 text-sm text-slate-500">Nothing stands out this month.</p>
        ) : (
          <ol key="list" className="mt-2 divide-y divide-slate-100">
            {view.attention.map((a, i) => {
              const listing = a.listing_id !== null ? view.listings.find((l) => l.listing_id === a.listing_id) : undefined;
              return (
                <li key={`${a.kind}-${a.listing_id ?? i}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-900">{a.title}</p>
                    {listing && (
                      <p key="listing" className="truncate text-xs text-slate-600">
                        <span translate="no">{listing.title ?? `Listing ${listing.listing_id}`}</span>
                        <span> · </span>
                        <a href={listing.url} target="_blank" rel="noreferrer" className="tap text-brand-700 hover:underline">View on Etsy ↗</a>
                      </p>
                    )}
                    <p className="mt-0.5 text-[13px] text-slate-600">{a.why}</p>
                    <p className="mt-0.5 text-[13px] text-slate-500">{a.do}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-slate-500" title={TOTALS.stake.text}>At stake</p>
                    <p className="inline-flex items-center gap-1.5 text-sm">
                      <span translate="no" className="font-semibold tabular-nums text-slate-900">{a.stake_minor !== null ? money(a.stake_minor, c) : "—"}</span>
                      <BasisTag basis={a.basis} />
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <section className="card p-4 text-sm sm:p-5" aria-labelledby="receipt-title">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-x-3 border-b border-slate-200 pb-2 sm:grid-cols-[minmax(0,1fr)_9.5rem_9.5rem_9.5rem]">
          <h2 id="receipt-title" className="font-medium text-slate-900">{`${name}, line by line`}</h2>
          <p className="text-right text-xs font-medium text-slate-700">{monthName(view.month, true)}</p>
          <p className="hidden text-right text-xs text-slate-500 sm:block">{view.last_month ? monthName(view.last_month.month, true) : "Last month"}</p>
          <p className="hidden text-right text-xs text-slate-500 sm:block">{view.last_year ? monthName(view.last_year.month, true) : "A year ago"}</p>
        </div>

        {sheet.sections.map((s) => (
          <div key={s.key} className="pt-3">
            {s.key === "your_costs" && (
              <Row key="net" def={TOTALS.net_etsy} figure={sheet.net_etsy} view={view} strong />
            )}
            <p className="mt-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">{SECTIONS[s.key]?.label ?? s.key}</p>
            {s.lines.map((line) => (
              <Row
                key={line.key}
                def={LINES[line.key] ?? { label: line.key, text: "" }}
                figure={line}
                view={view}
                extra={
                  line.key === "refunds" && refunds?.parts
                    ? refundsParts(money(Math.abs(refunds.parts.refunded ?? 0), c), money(Math.abs(refunds.parts.tax_returned ?? 0), c))
                    : null
                }
              />
            ))}
            <Row def={{ label: `${SECTIONS[s.key]?.label ?? s.key}, total`, text: SECTIONS[s.key]?.text ?? "" }} figure={s.total} view={view} strong />
          </div>
        ))}
        <div className="mt-2 border-t-2 border-slate-900">
          <Row def={sheet.profit.minor !== null ? TOTALS.profit : { ...TOTALS.profit, label: "Profit (needs product cost)" }} figure={sheet.profit} view={view} strong />
        </div>

        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-500">
          {(Object.keys(BASIS) as Basis[]).map((b) => (
            <span key={b} className="inline-flex items-center gap-1.5">
              <BasisTag basis={b} />
              <span>{BASIS[b].text}</span>
            </span>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <section className="card space-y-2 p-4 text-sm" aria-labelledby="ads-title">
          <h2 id="ads-title" className="font-medium text-slate-900">Etsy Ads, for the whole shop</h2>
          <div className="flex items-baseline justify-between gap-3">
            <Term def={TOTALS.ads_charged} note={ADS_LAG} />
            <Value figure={charged} currency={c} />
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <Term def={TOTALS.ads_clicks} note={ADS_LAG} />
            {sheet.ads_report.spend_minor !== null ? (
              <span key="spend" className="inline-flex items-center gap-1.5">
                <span translate="no" className="tabular-nums">{money(sheet.ads_report.spend_minor, c)}</span>
                <BasisTag basis="exact" />
              </span>
            ) : (
              <span key="none" className="text-slate-400" title="Import the Etsy Ads report for this month">—</span>
            )}
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <Term def={TOTALS.actual_roas} note={be.actual_note} />
            <span translate="no" className="tabular-nums">{be.actual_roas !== null ? be.actual_roas.toFixed(2) : "—"}</span>
          </div>
          <p className="text-xs text-slate-500">
            <span>
              {sheet.ads_report.days > 0 && sheet.ads_report.days < sheet.ads_report.month_days
                ? `The Ads report covers ${sheet.ads_report.days} of the month's ${sheet.ads_report.month_days} days. `
                : ""}
            </span>
            <span>Ad spend is never given to listings: Etsy&apos;s report has no listing column. Change your ads in Shop Manager; nothing is changed from here.</span>
          </p>
        </section>

        <section className="card space-y-2 p-4 text-sm" aria-labelledby="left-title">
          <h2 id="left-title" className="font-medium text-slate-900"><Term def={TOTALS.not_attributed} /></h2>
          {sheet.source !== "statement" ? (
            <p key="need" className="text-xs text-slate-500">Tying amounts to listings needs the month&apos;s statement.</p>
          ) : groups.length === 0 && !left.etsy_ads_minor ? (
            <p key="all" className="text-xs text-slate-500">Every amount on the statement is tied to a listing.</p>
          ) : (
            <ul key="list" className="space-y-1.5">
              {groups.map(([group, minor]) => (
                <li key={group} className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 text-slate-700">
                    <span>{GROUP_LABEL[group] ?? group}</span>
                    <Txt>{group === "revenue" && left.orders ? ` (${left.orders} ${left.orders === 1 ? "order" : "orders"})` : ""}</Txt>
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span translate="no" className="tabular-nums">{money(minor, c)}</span>
                    <BasisTag basis="calculated" />
                  </span>
                </li>
              ))}
              {left.etsy_ads_minor !== null && left.etsy_ads_minor !== 0 && (
                <li key="ads" className="flex items-baseline justify-between gap-3">
                  <span className="text-slate-700">Etsy Ads (a cost of the whole shop)</span>
                  <span className="inline-flex items-center gap-1.5">
                    <span translate="no" className="tabular-nums">{money(left.etsy_ads_minor, c)}</span>
                    <BasisTag basis={charged?.basis} />
                  </span>
                </li>
              )}
            </ul>
          )}
          {sheet.deposits_minor !== null && (
            <div key="deposits" className="flex items-baseline justify-between gap-3 border-t border-slate-100 pt-2">
              <Term def={TOTALS.deposits} />
              <span className="inline-flex items-center gap-1.5">
                <span translate="no" className="tabular-nums">{money(Math.abs(sheet.deposits_minor), c)}</span>
                <BasisTag basis="exact" />
              </span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
