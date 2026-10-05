"use client";

import { useMemo, useState } from "react";
import { money } from "@/lib/analytics";
import { CLASSES, COLUMNS } from "@/lib/analyticsDefinitions";
import type { MonthClass, MonthListing, MonthView } from "@/lib/types";
import { BasisTag, Term, monthName } from "./Month";

import { Txt } from "@/components/Txt";

/**
 * Every listing's month: its class with the reason, what one sale is worth,
 * and its last six months. Sortable; cards on a phone. Results are before Etsy
 * Ads (a cost of the whole shop). Each listing links back to Etsy.
 */

const CLASS_STYLE: Record<MonthClass, string> = {
  winner: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  steady: "bg-slate-50 text-slate-700 ring-slate-200",
  fading: "bg-amber-50 text-amber-800 ring-amber-200",
  losing: "bg-rose-50 text-rose-800 ring-rose-200",
  new: "bg-sky-50 text-sky-800 ring-sky-200",
};
const CLASS_ORDER: MonthClass[] = ["losing", "fading", "winner", "steady", "new"];

type SortKey = "result" | "units" | "revenue" | "fees" | "per_unit" | "title" | "class";
const SORTS: { key: SortKey; label: string }[] = [
  { key: "result", label: "Profit before ads" },
  { key: "per_unit", label: "Per item" },
  { key: "units", label: "Sold" },
  { key: "revenue", label: "Sales" },
  { key: "fees", label: "Etsy fees" },
  { key: "class", label: "Class" },
  { key: "title", label: "Name" },
];

const best = (r: MonthListing) => (r.result_minor !== null ? r.result_minor : r.before_cost_minor);

function sortValue(r: MonthListing, key: SortKey): number | string | null {
  switch (key) {
    case "result": return best(r);
    case "per_unit": return r.per_unit_minor;
    case "units": return r.units;
    case "revenue": return r.revenue_minor;
    case "fees": return r.fees_minor === null ? null : -r.fees_minor;
    case "class": return CLASS_ORDER.indexOf(r.class);
    case "title": return (r.title ?? `~${r.listing_id}`).toLowerCase();
  }
}

export function ClassBadge({ klass }: { klass: MonthClass }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${CLASS_STYLE[klass]}`} title={CLASSES[klass].text}>
      {CLASSES[klass].label}
    </span>
  );
}

/** Items sold in each of the last six months. One series: the header names it. */
function Trend({ values, months }: { values: number[]; months: string[] }) {
  const max = Math.max(1, ...values);
  const text = values.map((v, i) => `${months[i] ? monthName(months[i], true) : ""}: ${v}`).join(", ");
  return (
    <span className="inline-flex items-end gap-[2px]" role="img" aria-label={`Sold per month. ${text}`} title={text}>
      {values.map((v, i) => (
        <span
          key={i}
          className={`inline-block w-[7px] rounded-t-[2px] ${i === values.length - 1 ? "bg-brand-600" : "bg-slate-300"}`}
          style={{ height: `${Math.max(2, Math.round((v / max) * 22))}px` }}
        />
      ))}
    </span>
  );
}

function Money({ minor, currency }: { minor: number | null; currency: string | null }) {
  if (minor === null) return <span className="text-slate-400">—</span>;
  return <span translate="no" className="tabular-nums">{money(minor, currency)}</span>;
}

function Name({ row }: { row: MonthListing }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      {row.thumbnail_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img key="img" src={row.thumbnail_url} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
      ) : (
        <span key="blank" className="h-9 w-9 shrink-0 rounded bg-slate-100" aria-hidden />
      )}
      <div className="min-w-0">
        <p className="truncate text-slate-800" title={row.title ?? undefined} translate="no">{row.title ?? `Listing ${row.listing_id}`}</p>
        <p className="truncate text-xs text-slate-400">
          <Txt>{row.sku ? `${row.sku} · ` : ""}</Txt>
          <Txt>{row.profile_name ? `${row.profile_name} · ` : ""}</Txt>
          {/* ToU: wherever a listing is shown, it links back to Etsy. */}
          <a href={row.url} target="_blank" rel="noreferrer" className="tap text-brand-700 hover:underline">View on Etsy ↗</a>
        </p>
      </div>
    </div>
  );
}

export function MonthListings({ view, onCosts }: { view: MonthView; onCosts: () => void }) {
  const [sort, setSort] = useState<SortKey>("result");
  const [down, setDown] = useState(true);
  const [only, setOnly] = useState<MonthClass | null>(null);
  const c = view.currency;
  const statement = view.sheet.source === "statement";

  const rows = useMemo(() => {
    const list = view.listings.filter((r) => !only || r.class === only);
    return [...list].sort((a, b) => {
      const x = sortValue(a, sort), y = sortValue(b, sort);
      if (x === null && y === null) return 0;
      if (x === null) return 1; // blanks last, whichever way it is sorted
      if (y === null) return -1;
      const d = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return (sort === "title" || sort === "class" ? !down : down) ? -d : d;
    });
  }, [view.listings, only, sort, down]);

  const pick = (key: SortKey) => {
    if (key === sort) setDown((v) => !v);
    else {
      setSort(key);
      setDown(true);
    }
  };
  const Head = ({ k, def, right = true }: { k: SortKey; def: { label: string; text: string }; right?: boolean }) => (
    <th scope="col" className={`px-2 py-2 font-medium ${right ? "text-right" : "text-left"}`} aria-sort={sort === k ? (down ? "descending" : "ascending") : "none"}>
      <button type="button" className="inline-flex items-center gap-1 hover:text-slate-900" title={def.text} onClick={() => pick(k)}>
        <span>{def.label}</span>
        <span aria-hidden className={sort === k ? "text-slate-700" : "text-transparent"}>{down ? "↓" : "↑"}</span>
      </button>
    </th>
  );
  const uncosted = view.listings.filter((r) => !r.costed && r.units > 0).length;

  if (view.listings.length === 0) {
    return (
      <div className="card p-5 text-sm text-slate-500">
        <span>{`No sales are tied to listings for ${monthName(view.month)}. `}</span>
        <span>{view.sheet.source === "none" ? "Read the shop's sales or import the month's statement." : "Once the shop's sales are read, each listing's month appears here."}</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Show a class">
        <button type="button" onClick={() => setOnly(null)} aria-pressed={only === null}
          className={`rounded-full border px-3 py-1 text-xs max-sm:min-h-[2.75rem] ${only === null ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-600"}`}>
          <span>{`All ${view.listings.length}`}</span>
        </button>
        {CLASS_ORDER.filter((k) => view.classes[k]).map((k) => (
          <button key={k} type="button" onClick={() => setOnly(only === k ? null : k)} aria-pressed={only === k} title={CLASSES[k].text}
            className={`rounded-full border px-3 py-1 text-xs max-sm:min-h-[2.75rem] ${only === k ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-600"}`}>
            <span>{`${CLASSES[k].label} ${view.classes[k]}`}</span>
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-xs text-slate-500 sm:hidden">
          <span>Sort by</span>
          <select className="field py-1" value={sort} onChange={(e) => { setSort(e.target.value as SortKey); setDown(true); }}>
            {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
      </div>

      <p className="text-xs text-slate-500">
        <span>{`${monthName(view.month)}. Results are before Etsy Ads, which is a cost of the whole shop. `}</span>
        <Txt>{statement ? "" : "Etsy's fees per listing need the month's statement: until it is imported, those columns are blank. "}</Txt>
        {uncosted > 0 && (
          <span key="uncosted">
            <span>{`${uncosted} ${uncosted === 1 ? "listing has" : "listings have"} no product cost, so their result is before product cost. `}</span>
            <button type="button" className="tap underline hover:text-slate-900" onClick={onCosts}>Enter product costs</button>
          </span>
        )}
      </p>

      {/* Wide screens: the table. */}
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full min-w-[60rem] text-sm">
          <thead className="border-b border-slate-200 text-xs text-slate-500">
            <tr>
              <Head k="title" def={{ label: "Listing", text: "The listing, its SKU and profile, and its page on Etsy." }} right={false} />
              <Head k="class" def={COLUMNS.class} right={false} />
              <Head k="units" def={COLUMNS.units} />
              <Head k="revenue" def={COLUMNS.revenue} />
              <Head k="fees" def={COLUMNS.fees} />
              <th scope="col" className="px-2 py-2 text-right font-medium" title={COLUMNS.product_cost.text}>{COLUMNS.product_cost.label}</th>
              <Head k="result" def={COLUMNS.result} />
              <Head k="per_unit" def={COLUMNS.per_unit} />
              <th scope="col" className="px-2 py-2 text-right font-medium" title={COLUMNS.trend.text}>{COLUMNS.trend.label}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.listing_id} className="align-top">
                <td className="max-w-[18rem] px-2 py-2"><Name row={r} /></td>
                <td className="max-w-[16rem] px-2 py-2">
                  <ClassBadge klass={r.class} />
                  <p className="mt-1 text-xs text-slate-500">{r.reason}</p>
                </td>
                <td className="px-2 py-2 text-right tabular-nums" translate="no">
                  <span>{r.units}</span>
                  <span className="block text-xs text-slate-400">{`${r.units_before} before`}</span>
                </td>
                <td className="px-2 py-2 text-right"><Money minor={r.revenue_minor} currency={c} /></td>
                <td className="px-2 py-2 text-right">
                  <Money minor={r.fees_minor === null ? null : r.fees_minor + (r.offsite_ads_minor ?? 0)} currency={c} />
                  {r.refunds_minor ? <span key="refunds" className="block text-xs text-slate-400" translate="no">{`refunds ${money(r.refunds_minor, c)}`}</span> : null}
                </td>
                <td className="px-2 py-2 text-right">
                  {r.product_cost_minor !== null ? <Money key="cost" minor={r.product_cost_minor} currency={c} /> : <span key="none" className="text-xs text-amber-700">not entered</span>}
                </td>
                <td className="px-2 py-2 text-right">
                  <span className="inline-flex items-center justify-end gap-1.5 font-medium text-slate-900">
                    <Money minor={best(r)} currency={c} />
                    <BasisTag basis={best(r) !== null ? r.basis : null} />
                  </span>
                  {r.result_minor === null && r.before_cost_minor !== null && <span key="pre" className="block text-xs text-amber-700">before product cost</span>}
                </td>
                <td className="px-2 py-2 text-right"><Money minor={r.per_unit_minor} currency={c} /></td>
                <td className="px-2 py-2 text-right"><Trend values={r.trend} months={view.trend_months} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Phone: a card each, the same figures. */}
      <ul className="space-y-2 sm:hidden">
        {rows.map((r) => (
          <li key={r.listing_id} className="card space-y-2 p-3 text-sm">
            <Name row={r} />
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <ClassBadge klass={r.class} />
                <p className="mt-1 text-xs text-slate-500">{r.reason}</p>
              </div>
              <Trend values={r.trend} months={view.trend_months} />
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[13px]">
              <dt className="text-slate-500"><Term def={COLUMNS.units} /></dt>
              <dd className="text-right tabular-nums" translate="no">{`${r.units} (${r.units_before} before)`}</dd>
              <dt className="text-slate-500"><Term def={COLUMNS.revenue} /></dt>
              <dd className="text-right"><Money minor={r.revenue_minor} currency={c} /></dd>
              <dt className="text-slate-500"><Term def={COLUMNS.fees} /></dt>
              <dd className="text-right"><Money minor={r.fees_minor === null ? null : r.fees_minor + (r.offsite_ads_minor ?? 0)} currency={c} /></dd>
              <dt className="text-slate-500"><Term def={COLUMNS.refunds} /></dt>
              <dd className="text-right"><Money minor={r.refunds_minor} currency={c} /></dd>
              <dt className="text-slate-500"><Term def={COLUMNS.product_cost} /></dt>
              <dd className="text-right">
                {r.product_cost_minor !== null ? <Money key="cost" minor={r.product_cost_minor} currency={c} /> : <span key="none" className="text-xs text-amber-700">not entered</span>}
              </dd>
              <dt className="font-medium text-slate-900"><Term def={r.result_minor === null ? { ...COLUMNS.result, label: "Before product cost" } : COLUMNS.result} /></dt>
              <dd className="inline-flex items-center justify-end gap-1.5 font-medium text-slate-900">
                <Money minor={best(r)} currency={c} />
                <BasisTag basis={best(r) !== null ? r.basis : null} />
              </dd>
              <dt className="text-slate-500"><Term def={COLUMNS.per_unit} /></dt>
              <dd className="text-right"><Money minor={r.per_unit_minor} currency={c} /></dd>
            </dl>
          </li>
        ))}
      </ul>

      <p className="text-xs text-slate-400">
        {CLASS_ORDER.map((k) => (
          <span key={k}>
            <b className="font-medium text-slate-500">{CLASSES[k].label}</b>
            <span>{`: ${CLASSES[k].text} `}</span>
          </span>
        ))}
      </p>
    </div>
  );
}
