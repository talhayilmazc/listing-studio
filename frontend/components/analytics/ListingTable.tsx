"use client";

import { useMemo, useState } from "react";
import { CLASS_ORDER, filterRows, money, percent, type SortKey } from "@/lib/analytics";
import type { AnalyticsRow, ListingClass } from "@/lib/types";
import { ClassBadge, Delta, ListingCell } from "./Shared";

const PAGE = 50;

const COLUMNS: { key: SortKey; label: string; title?: string }[] = [
  { key: "units", label: "Sold" },
  { key: "revenue", label: "Revenue" },
  { key: "net", label: "Net" },
  { key: "margin", label: "Margin" },
  { key: "ad_spend", label: "Ad spend" },
  { key: "acos", label: "ACOS", title: "Ad spend ÷ the revenue the ads brought in" },
  { key: "change", label: "vs before", title: "Revenue against the previous period" },
];

/** Every listing with its figures and class: sortable, filterable, most urgent first. */
export function ListingTable({
  rows,
  currency,
  days,
  classes,
  onClasses,
}: {
  rows: AnalyticsRow[];
  currency: string | null;
  days: number;
  classes: ListingClass[];
  onClasses: (c: ListingClass[]) => void;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("attention");
  const [desc, setDesc] = useState(true);
  const [page, setPage] = useState(0);

  const shown = useMemo(() => filterRows(rows, { classes, q, sort, desc }), [rows, classes, q, sort, desc]);
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const current = Math.min(page, pages - 1);
  const counts = useMemo(() => {
    const m: Partial<Record<ListingClass, number>> = {};
    for (const r of rows) m[r.verdict.klass] = (m[r.verdict.klass] ?? 0) + 1;
    return m;
  }, [rows]);

  const sortBy = (key: SortKey) => {
    if (sort === key) setDesc(!desc);
    else {
      setSort(key);
      setDesc(true);
    }
    setPage(0);
  };
  const toggle = (k: ListingClass) => {
    onClasses(classes.includes(k) ? classes.filter((c) => c !== k) : [...classes, k]);
    setPage(0);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          className="field w-full py-1.5 sm:w-64"
          placeholder="Search title, SKU, profile or number"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(0);
          }}
        />
        {CLASS_ORDER.filter((k) => counts[k]).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => toggle(k)}
            aria-pressed={classes.includes(k)}
            className={
              "flex items-center gap-1.5 rounded-full border px-1 py-0.5 pr-2 text-xs " +
              (classes.includes(k) ? "border-brand-500 bg-brand-50" : "border-transparent hover:border-slate-300")
            }
          >
            <ClassBadge klass={k} />
            <span className="tabular-nums text-slate-600">{counts[k]}</span>
          </button>
        ))}
        {classes.length > 0 && (
          <button type="button" className="text-xs text-slate-500 underline" onClick={() => onClasses([])}>
            all classes
          </button>
        )}
        <span className="ml-auto text-xs text-slate-500">
          {shown.length.toLocaleString()} listing{shown.length === 1 ? "" : "s"}
          {sort === "attention" && " · most in need of attention first"}
        </span>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[56rem] text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">
                <button type="button" onClick={() => sortBy("attention")} className="hover:text-slate-900">
                  Listing {sort === "attention" && "↓"}
                </button>
              </th>
              <th className="px-3 py-2 font-medium">Class</th>
              {COLUMNS.map((c) => (
                <th key={c.key} className="px-3 py-2 text-right font-medium" title={c.title}>
                  <button type="button" onClick={() => sortBy(c.key)} className="hover:text-slate-900">
                    {c.label} {sort === c.key && (desc ? "↓" : "↑")}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {shown.slice(current * PAGE, (current + 1) * PAGE).map((r) => (
              <tr key={r.listing_id} className="align-top hover:bg-slate-50/60">
                <td className="max-w-[22rem] px-3 py-2">
                  <ListingCell row={r} days={days} />
                </td>
                <td className="px-3 py-2">
                  <span title={`${r.verdict.reason} ${r.verdict.action}`}>
                    <ClassBadge klass={r.verdict.klass} />
                  </span>
                </td>
                <Num>{r.current.units}</Num>
                <Num>{money(r.current.revenue, currency)}</Num>
                <Num tone={r.current.net < 0 ? "text-rose-700" : "text-slate-900 font-medium"}>{money(r.current.net, currency)}</Num>
                <Num>{percent(r.current.margin)}</Num>
                <Num>{r.current.ad_spend ? money(r.current.ad_spend, currency) : "—"}</Num>
                <Num>{percent(r.current.acos)}</Num>
                <Num>
                  <Delta change={r.revenue_change} current={r.current.revenue} />
                </Num>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-slate-400">
                  No listing matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button type="button" className="btn-secondary px-2 py-1" disabled={current === 0} onClick={() => setPage(current - 1)}>
            Previous
          </button>
          <span className="tabular-nums text-slate-500">
            {current + 1} / {pages}
          </span>
          <button type="button" className="btn-secondary px-2 py-1" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function Num({ children, tone = "text-slate-700" }: { children: React.ReactNode; tone?: string }) {
  return <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${tone}`}>{children}</td>;
}
