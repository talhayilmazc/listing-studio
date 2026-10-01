"use client";

import { Fragment, useMemo, useState } from "react";
import { CLASS_LABEL, CLASS_ORDER, type SortKey, change, filterRows, money, percent } from "@/lib/analytics";
import type { ListingClass, ListingRow } from "@/lib/types";
import { ClassBadge, Delta, ExportLink, ListingCell, TrendBadge } from "./Shared";
import { UnitEconomics } from "./UnitEconomics";

import { Txt } from "@/components/Txt";
const PAGE = 50;

const SORTS: [SortKey, string][] = [
  ["stake", "Money at stake"],
  ["net", "Net profit"],
  ["revenue", "Revenue"],
  ["units", "Items sold"],
  ["margin", "Margin"],
  ["net_per_unit", "Net per item"],
  ["ads", "Ad spend"],
  ["acos", "ACOS"],
  ["change", "Change in revenue"],
  ["launched", "Launch date"],
];

const blank = <span className="text-slate-300">—</span>;

function Acos({ row }: { row: ListingRow }) {
  const e = row.economics;
  if (!e || e.acos === null) return blank;
  const over = e.break_even_acos !== null && e.acos > e.break_even_acos;
  return (
    <span translate="no" className="tabular-nums">
      <span className={over ? "font-medium text-rose-700" : ""}>{percent(e.acos)}</span>
      <span className="text-xs text-slate-400">{` / ${percent(e.break_even_acos)}`}</span>
    </span>
  );
}

/**
 * Every listing's unit economics, most money at stake first. A row opens its
 * fees itemised; the CSV has every column for all of them.
 */
export function ListingTable({
  rows,
  currency,
  days,
  comparisonLabel,
  classes,
  onClasses,
  exportHref,
}: {
  rows: ListingRow[];
  currency: string | null;
  days: number;
  comparisonLabel: string | undefined;
  classes: ListingClass[];
  onClasses: (c: ListingClass[]) => void;
  exportHref: string;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("stake");
  const [desc, setDesc] = useState(true);
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<number | null>(null);

  const counts = useMemo(() => {
    const c: Partial<Record<ListingClass, number>> = {};
    for (const r of rows) if (r.status) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rows]);
  const filtered = useMemo(() => filterRows(rows, { classes, q, sort, desc }), [rows, classes, q, sort, desc]);
  const page = filtered.slice(0, shown);
  const unread = rows.length > 0 && rows.every((r) => r.economics === null);

  const toggle = (k: ListingClass) => {
    onClasses(classes.includes(k) ? classes.filter((c) => c !== k) : [...classes, k]);
    setShown(PAGE);
  };
  const sortBy = (k: SortKey) => {
    if (k === sort) setDesc(!desc);
    else {
      setSort(k);
      setDesc(true);
    }
    setShown(PAGE);
  };
  const Th = ({ k, children, className = "" }: { k: SortKey; children: React.ReactNode; className?: string }) => (
    <th className={`px-2 pb-2 text-right font-medium ${className}`} aria-sort={sort === k ? (desc ? "descending" : "ascending") : undefined}>
      <button type="button" onClick={() => sortBy(k)} className={`whitespace-nowrap hover:text-slate-700 ${sort === k ? "text-slate-800" : ""}`}>
        <span>{children}</span>
        <Txt>{sort === k ? (desc ? " ↓" : " ↑") : ""}</Txt>
      </button>
    </th>
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setShown(PAGE);
          }}
          placeholder="Search title, SKU, profile or number"
          className="field w-full py-1.5 sm:w-72"
          aria-label="Search listings"
        />
        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          <span>Sort by</span>
          <select className="field w-auto py-1" value={sort} onChange={(e) => sortBy(e.target.value as SortKey)}>
            {SORTS.map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
        </label>
        <span className="text-xs text-slate-500 sm:ml-auto" translate="no">
          {`${filtered.length.toLocaleString()} of ${rows.length.toLocaleString()} listings`}
        </span>
        <ExportLink href={exportHref} label="Export all as CSV" />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {CLASS_ORDER.filter((k) => counts[k]).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => toggle(k)}
            aria-pressed={classes.includes(k)}
            className={
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs " +
              (classes.includes(k) ? "border-slate-800 bg-slate-800 text-white" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300")
            }
          >
            <span>{CLASS_LABEL[k]}</span>
            <span translate="no" className="tabular-nums opacity-70">{counts[k]}</span>
          </button>
        ))}
        {classes.length > 0 && (
          <button key="clear" type="button" className="px-2 text-xs text-brand-700 hover:underline" onClick={() => onClasses([])}>
            Clear
          </button>
        )}
      </div>

      {unread && (
        <p key="unread" className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
          Your sales haven&apos;t been read, so each listing&apos;s figures are blank rather than zero. Read your sales above.
        </p>
      )}
      {comparisonLabel && <p key="cmp" className="text-xs text-slate-500">{`Change: ${comparisonLabel.toLowerCase()}.`}</p>}

      {filtered.length === 0 ? (
        <div className="card p-6 text-sm text-slate-500">No listing matches.</div>
      ) : (
        <>
          {/* Wide screens: the table. */}
          <div className="card hidden p-3 xl:block">
            <table className="w-full table-fixed text-sm">
              <thead className="text-left text-xs text-slate-400">
                <tr>
                  <th className="px-2 pb-2 font-medium">Listing</th>
                  <th className="w-36 px-2 pb-2 font-medium">Status · 4-week trend</th>
                  <Th k="units" className="w-14">Sold</Th>
                  <Th k="revenue" className="w-28">Revenue</Th>
                  <Th k="net" className="w-24">Net</Th>
                  <Th k="margin" className="w-16">Margin</Th>
                  <Th k="net_per_unit" className="w-20">Net / item</Th>
                  <Th k="acos" className="w-24">ACOS / b-e</Th>
                  <Th k="stake" className="w-24">At stake</Th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {page.map((r) => {
                  const e = r.economics;
                  const isOpen = open === r.listing_id;
                  return (
                    <Fragment key={r.listing_id}>
                      <tr className={isOpen ? "bg-slate-50" : ""}>
                        <td className="px-2 py-2"><ListingCell row={r} days={days} /></td>
                        <td className="px-2 py-2">
                          <span className="flex flex-col items-start gap-1">
                            {r.status ? <ClassBadge klass={r.status} /> : blank}
                            <TrendBadge trend={r.trend} />
                          </span>
                        </td>
                        <td translate="no" className="px-2 py-2 text-right tabular-nums">{e ? e.units.toLocaleString() : blank}</td>
                        <td className="px-2 py-2 text-right">
                          <span translate="no" className="block tabular-nums">{e ? money(e.revenue, currency) : blank}</span>
                          {e && r.comparison && (
                            <span key="d" className="block text-xs"><Delta change={change(e.revenue, r.comparison.revenue)} current={e.revenue} /></span>
                          )}
                        </td>
                        <td translate="no" className={`px-2 py-2 text-right tabular-nums ${e && e.net < 0 ? "text-rose-700" : ""}`}>
                          {e ? money(e.net, currency) : blank}
                        </td>
                        <td translate="no" className="px-2 py-2 text-right tabular-nums">{e && e.margin !== null ? percent(e.margin) : blank}</td>
                        <td translate="no" className="px-2 py-2 text-right tabular-nums">{e && e.net_per_unit !== null ? money(e.net_per_unit, currency) : blank}</td>
                        <td className="px-2 py-2 text-right"><Acos row={r} /></td>
                        <td translate="no" className="px-2 py-2 text-right font-medium tabular-nums">{r.action ? money(r.action.stake, currency) : blank}</td>
                        <td className="py-2 text-right">
                          <button
                            type="button"
                            onClick={() => setOpen(isOpen ? null : r.listing_id)}
                            aria-expanded={isOpen}
                            aria-label={isOpen ? "Hide the fees" : "Show the fees itemised"}
                            className="rounded px-1.5 py-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                          >
                            {isOpen ? "▴" : "▾"}
                          </button>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr key="detail" className="bg-slate-50">
                          <td colSpan={10} className="px-4 pb-4 pt-1"><Detail row={r} currency={currency} /></td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Narrower screens: one card per listing, every figure within reach. */}
          <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:hidden">
            {page.map((r) => {
              const e = r.economics;
              const isOpen = open === r.listing_id;
              return (
                <li key={r.listing_id} className={`card min-w-0 space-y-3 p-4 ${isOpen ? "lg:col-span-2" : ""}`}>
                  <ListingCell row={r} days={days} />
                  <div className="flex flex-wrap items-center gap-2">
                    {r.status && <ClassBadge key="status" klass={r.status} />}
                    <TrendBadge trend={r.trend} />
                    {r.action && (
                      <span key="stake" className="ml-auto text-xs text-slate-500">
                        <span translate="no" className="text-sm font-semibold tabular-nums text-slate-900">{money(r.action.stake, currency)}</span>
                        <span> at stake / 30 days</span>
                      </span>
                    )}
                  </div>
                  <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-sm">
                    <Fig label="Revenue">
                      {e ? <span translate="no">{money(e.revenue, currency)}</span> : blank}
                      {e && r.comparison && <span key="d" className="ml-1 text-xs"><Delta change={change(e.revenue, r.comparison.revenue)} current={e.revenue} /></span>}
                    </Fig>
                    <Fig label="Net">{e ? <span translate="no" className={e.net < 0 ? "text-rose-700" : ""}>{money(e.net, currency)}</span> : blank}</Fig>
                    <Fig label="Sold">{e ? <span translate="no">{e.units.toLocaleString()}</span> : blank}</Fig>
                    <Fig label="Margin">{e && e.margin !== null ? <span translate="no">{percent(e.margin)}</span> : blank}</Fig>
                    <Fig label="Net / item">{e && e.net_per_unit !== null ? <span translate="no">{money(e.net_per_unit, currency)}</span> : blank}</Fig>
                    <Fig label="ACOS / break-even"><Acos row={r} /></Fig>
                  </dl>
                  <button type="button" onClick={() => setOpen(isOpen ? null : r.listing_id)} aria-expanded={isOpen} className="text-xs font-medium text-brand-700 hover:underline">
                    {isOpen ? "Hide the fees" : "Fees itemised"}
                  </button>
                  {isOpen && <Detail key="detail" row={r} currency={currency} />}
                </li>
              );
            })}
          </ul>

          {filtered.length > shown && (
            <button key="more" type="button" className="btn-secondary" onClick={() => setShown(shown + PAGE * 2)}>
              <span>Show more (<span translate="no">{(filtered.length - shown).toLocaleString()}</span> left)</span>
            </button>
          )}
        </>
      )}
    </div>
  );
}

function Fig({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-slate-500">{label}</dt>
      <dd className="tabular-nums text-slate-900">{children}</dd>
    </div>
  );
}

function Detail({ row, currency }: { row: ListingRow; currency: string | null }) {
  return (
    <div className="space-y-3">
      {row.action && (
        <p key="action" className="text-sm text-slate-700">
          <span>{row.action.reason}</span> <span className="font-medium text-slate-900">{row.action.action}</span>
        </p>
      )}
      {row.economics ? (
        <UnitEconomics cur={row.economics} currency={currency} />
      ) : (
        <p className="text-sm text-slate-500">No figures: this shop&apos;s sales haven&apos;t been read.</p>
      )}
    </div>
  );
}
