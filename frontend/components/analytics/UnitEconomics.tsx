"use client";

import { SOURCE_LABEL, change, money, percent } from "@/lib/analytics";
import type { Economics } from "@/lib/types";
import { Delta, SourceTag } from "./Shared";

import { Txt } from "@/components/Txt";
const blank = <span className="text-slate-300">blank</span>;

function Row({
  label,
  note,
  cur,
  prev,
  currency,
  minus = true,
  upIsGood = false,
  source,
  showPrev,
}: {
  label: string;
  note?: string | null;
  cur: number | null | undefined;
  prev: number | null | undefined;
  currency: string | null;
  minus?: boolean;
  upIsGood?: boolean;
  source?: string;
  showPrev: boolean;
}) {
  const fmt = (v: number | null | undefined) =>
    v === null || v === undefined ? blank : <span translate="no" className="tabular-nums">{minus && v ? `−${money(v, currency)}` : money(v, currency)}</span>;
  return (
    <tr>
      <td className="py-1.5 text-slate-700">
        <span><Txt>{minus ? "− " : ""}</Txt><span>{label}</span></span>{" "}
        {source && <SourceTag key="src" source={source} />}
        {note && <span key="note" className={`block text-xs ${cur === null || cur === undefined ? "text-amber-800" : "text-slate-500"}`}>{note}</span>}
      </td>
      <td className="py-1.5 text-right align-top text-slate-800">{fmt(cur)}</td>
      {showPrev && <td key="prev" className="py-1.5 text-right align-top text-slate-500">{fmt(prev)}</td>}
      {showPrev && (
        <td key="delta" className="w-14 py-1.5 text-right align-top text-xs">
          <Delta change={change(cur, prev)} current={cur} upIsGood={upIsGood} />
        </td>
      )}
    </tr>
  );
}

/**
 * One listing's unit economics: revenue, each fee, product cost, shipping, ad
 * spend, net, margin and net per item sold, then how its ads compare with the
 * most it can afford to spend (break-even ACOS is its own margin before ads).
 */
export function UnitEconomics({
  cur,
  prev,
  currency,
  prevLabel = "Before",
}: {
  cur: Economics;
  prev?: Economics | null;
  currency: string | null;
  prevLabel?: string;
}) {
  const showPrev = prev !== undefined;
  const p = prev ?? undefined;
  const feeSrc = cur.fees_source;
  const feeNote = feeSrc === "allocated"
    ? "This listing's share of what Etsy's ledger charged the shop."
    : "Estimated from your fee rates.";
  const over = cur.acos !== null && cur.break_even_acos !== null && cur.acos > cur.break_even_acos;
  return (
    <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-slate-400">
          <tr>
            <th className="pb-1 font-medium" />
            <th className="pb-1 text-right font-medium">This period</th>
            {showPrev && <th key="prev" className="pb-1 pl-4 text-right font-medium">{prevLabel}</th>}
            {showPrev && <th key="delta" className="pb-1" />}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          <Row label="Revenue" minus={false} upIsGood cur={cur.revenue} prev={p ? p.revenue : showPrev ? 0 : undefined} currency={currency} showPrev={showPrev}
            note={`${cur.units.toLocaleString()} sold in ${cur.orders.toLocaleString()} order lines; item price only, not buyer-paid shipping.`} />
          <Row label="Transaction fees" cur={cur.fees.transaction_fees} prev={p?.fees.transaction_fees} currency={currency} showPrev={showPrev} source={feeSrc} note={feeNote} />
          <Row label="Payment processing" cur={cur.fees.processing_fees} prev={p?.fees.processing_fees} currency={currency} showPrev={showPrev} source={feeSrc} />
          <Row label="Listing fees" cur={cur.fees.listing_fees} prev={p?.fees.listing_fees} currency={currency} showPrev={showPrev} source={feeSrc} />
          <Row label="Product cost" cur={cur.product} prev={p?.product} currency={currency} showPrev={showPrev}
            source={cur.product === null ? undefined : "costs"}
            note={cur.product === null ? "No product cost entered (Fees & costs)."
              : cur.unit_cost ? `${cur.unit_cost} per item${cur.unit_cost_source ? ` (${cur.unit_cost_source})` : ""}` : null} />
          <Row label="Shipping" cur={cur.shipping} prev={p?.shipping} currency={currency} showPrev={showPrev}
            source={cur.shipping === null ? undefined : "costs"} note={cur.shipping === null ? "No shipping cost entered." : null} />
          <Row label="Ad spend" cur={cur.ads} prev={p?.ads} currency={currency} showPrev={showPrev}
            source={cur.ads === null ? undefined : "report"}
            note={cur.ads === null ? "No Ads report covers this listing in this period; Etsy's ledger only has the shop-wide total." : null} />
          <tr className="font-semibold">
            <td className="py-2 text-slate-900">
              <span>Net profit</span>
              {cur.net_excludes.length > 0 && (
                <span key="excl" className="block text-xs font-normal text-amber-800">{`Leaves out: ${cur.net_excludes.join(", ")}.`}</span>
              )}
            </td>
            <td translate="no" className={`py-2 text-right align-top tabular-nums ${cur.net < 0 ? "text-rose-700" : "text-slate-900"}`}>{money(cur.net, currency)}</td>
            {showPrev && <td key="prev" translate="no" className="py-2 text-right align-top font-normal tabular-nums text-slate-500">{p ? money(p.net, currency) : money(0, currency)}</td>}
            {showPrev && (
              <td key="delta" className="py-2 text-right align-top text-xs font-normal">
                <Delta change={change(cur.net, p?.net)} current={cur.net} />
              </td>
            )}
          </tr>
        </tbody>
      </table>

      <dl className="grid grid-cols-2 content-start gap-x-4 gap-y-3 text-sm">
        <Stat label="Margin" value={percent(cur.margin)} />
        <Stat label="Net per item sold" value={money(cur.net_per_unit, currency)} />
        <Stat label="ACOS" value={percent(cur.acos)} hint="ad spend ÷ revenue from ads" tone={over ? "bad" : undefined} />
        <Stat label="Break-even ACOS" value={percent(cur.break_even_acos)} hint="this listing's margin before ads: above it, ads lose money" />
        <Stat label="Ad spend per ad sale" value={money(cur.spend_per_sale, currency)}
          hint={cur.ad_orders !== null ? `${cur.ad_orders} orders from ads` : undefined} />
        <Stat label="Revenue from ads" value={money(cur.ad_revenue, currency)} />
        {cur.acos === null && (
          <p key="noads" className="col-span-2 text-xs text-slate-500">
            {cur.ads === null
              ? "Ad figures need the Etsy Ads report for this period (Ads report tab)."
              : "ACOS is blank because the report shows no revenue from ads for this listing."}
          </p>
        )}
        <p className="col-span-2 text-[11px] text-slate-400">
          <span>{`Fees: ${SOURCE_LABEL[feeSrc] ?? feeSrc}.`}</span>
        </p>
      </dl>
    </div>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "bad" }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd translate="no" className={`text-base font-semibold tabular-nums ${tone === "bad" ? "text-rose-700" : "text-slate-900"}`}>{value}</dd>
      {hint && <dd key="hint" className="text-[11px] leading-snug text-slate-400">{hint}</dd>}
    </div>
  );
}
