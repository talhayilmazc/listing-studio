"use client";

import { useMemo, useState } from "react";
import { SourceLinks } from "@/components/marketing/ToolParts";
import { longDate } from "@/lib/marketing/content";
import { calculate, parseAmount, placeOf, showMoney, type OffsiteChoice } from "@/lib/freeTools/feeCalc";
import { CHECKED_AGAINST, DEFAULT_PLACE, FEES, FEES_CHECKED, NOT_INCLUDED, NOT_INCLUDED_SOURCES, OFFSITE_ADS, PROCESSING } from "@/lib/freeTools/fees";

/** An amount and its currency stay on one line; the result's box scrolls by itself if one ever does not fit. */
const AMOUNT = "whitespace-nowrap";
const LINK = "font-medium text-brand-700 underline decoration-brand-100 underline-offset-4 hover:decoration-brand-600";

function Amount({ id, label, value, onChange, hint }: { id: string; label: string; value: string; onChange: (value: string) => void; hint?: string }) {
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <input id={id} inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} className="field text-base" data-testid={id} autoComplete="off" />
      {hint ? <p key="hint" className="mt-1 text-sm text-slate-600">{hint}</p> : null}
    </div>
  );
}

/** One sale. Nothing leaves the page. */
export function FeeCalculator() {
  const [place, setPlace] = useState(DEFAULT_PLACE);
  const [price, setPrice] = useState("");
  const [shippingCharged, setShippingCharged] = useState("");
  const [giftWrap, setGiftWrap] = useState("");
  const [salesTax, setSalesTax] = useState("");
  const [baseCost, setBaseCost] = useState("");
  const [shippingCost, setShippingCost] = useState("");
  const [offsite, setOffsite] = useState<OffsiteChoice>("none");
  const [usdRate, setUsdRate] = useState("");
  const currency = placeOf(place)?.currency ?? "USD";
  const outcome = useMemo(() => calculate({
    place, offsite,
    price: parseAmount(price),
    shippingCharged: parseAmount(shippingCharged),
    giftWrap: parseAmount(giftWrap),
    salesTax: parseAmount(salesTax),
    baseCost: parseAmount(baseCost),
    shippingCost: parseAmount(shippingCost),
    usdRate: parseAmount(usdRate),
  }), [place, offsite, price, shippingCharged, giftWrap, salesTax, baseCost, shippingCost, usdRate]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 rounded-2xl border border-slate-200 bg-white p-4 sm:grid-cols-2 sm:p-5">
        <div className="sm:col-span-2">
          <label htmlFor="fee-place" className="label">Where your bank account is</label>
          <select id="fee-place" value={place} onChange={(e) => setPlace(e.target.value)} className="field text-base" data-testid="fee-place">
            {PROCESSING.map((row) => <option key={row.place} value={row.place}>{row.place}</option>)}
          </select>
          <p className="mt-1 text-sm text-slate-600" translate="no" data-testid="fee-currency">{`Enter every amount in ${currency}.`}</p>
        </div>
        <Amount id="fee-price" label="Your item price" value={price} onChange={setPrice} hint="Include any personalization charge." />
        <Amount id="fee-shipping-charged" label="Shipping the buyer pays" value={shippingCharged} onChange={setShippingCharged} />
        <Amount id="fee-gift-wrap" label="Gift wrap the buyer pays (optional)" value={giftWrap} onChange={setGiftWrap} />
        <Amount id="fee-base" label="Print-on-demand base cost" value={baseCost} onChange={setBaseCost} />
        <Amount id="fee-shipping-cost" label="Shipping you pay" value={shippingCost} onChange={setShippingCost} />
        <Amount id="fee-tax" label="Sales tax on the order (optional)" value={salesTax} onChange={setSalesTax} hint="For tax added at checkout: it counts only for payment processing, and it is not your income. If tax is already inside your price, leave this empty; the profit below then still includes that tax." />
        {currency !== "USD" && <Amount key="fee-rate" id="fee-rate" label={`1 USD in ${currency}`} value={usdRate} onChange={setUsdRate} hint="Etsy sets the listing fee and the Offsite Ads cap in US dollars." />}
        <div className="sm:col-span-2">
          <label htmlFor="fee-offsite" className="label">Did this order come from an Offsite Ad?</label>
          <select id="fee-offsite" value={offsite} onChange={(e) => setOffsite(e.target.value as OffsiteChoice)} className="field text-base" data-testid="fee-offsite">
            <option value="none">No</option>
            <option value="under">{`Yes, and my shop made under $10,000 in 365 days (${OFFSITE_ADS.under}%)`}</option>
            <option value="over">{`Yes, and my shop has made $10,000 or more in 365 days (${OFFSITE_ADS.over}%)`}</option>
          </select>
        </div>
      </div>

      <section aria-label="What this sale leaves" className="overflow-x-auto rounded-2xl border border-slate-200 bg-white p-4 sm:p-5" tabIndex={0} translate="no" data-testid="fee-result">
        {outcome.ok ? (
          <div key="result">
            <dl className="grid grid-cols-2 gap-3">
              <div><dt className="text-sm text-slate-600">Profit</dt><dd className={"font-display text-3xl text-slate-900 " + AMOUNT} data-testid="fee-profit">{showMoney(outcome.result.profit, outcome.result.currency)}</dd></div>
              <div><dt className="text-sm text-slate-600">Margin</dt><dd className="font-display text-3xl text-slate-900" data-testid="fee-margin">{`${outcome.result.margin}%`}</dd></div>
            </dl>
            <table className="mt-4 w-full border-collapse text-left text-[0.9375rem]">
              <caption className="sr-only">How the profit is worked out</caption>
              <tbody>
                <tr className="border-t border-slate-100"><th scope="row" className="py-2 pr-3 font-medium text-slate-900">Item, shipping and gift wrap the buyer pays</th><td className={"py-2 text-right text-slate-800 " + AMOUNT}>{showMoney(outcome.result.revenue, outcome.result.currency)}</td></tr>
                {outcome.result.fees.map((fee) => (
                  <tr key={fee.id} className="border-t border-slate-100" data-testid={`fee-line-${fee.id}`}>
                    <th scope="row" className="py-2 pr-3 font-normal text-slate-800"><span className="block">{fee.name}</span><span className="block text-sm text-slate-600">{fee.how}</span></th>
                    <td className={"py-2 text-right align-top text-slate-800 " + AMOUNT}>{`-${showMoney(fee.amount, outcome.result.currency)}`}</td>
                  </tr>
                ))}
                <tr className="border-t border-slate-200"><th scope="row" className="py-2 pr-3 font-medium text-slate-900">All Etsy fees above</th><td className={"py-2 text-right font-medium text-slate-900 " + AMOUNT} data-testid="fee-total">{showMoney(outcome.result.totalFees, outcome.result.currency)}</td></tr>
                <tr className="border-t border-slate-200"><th scope="row" className="py-2 pr-3 font-normal text-slate-800">Base cost and shipping you pay</th><td className={"py-2 text-right text-slate-800 " + AMOUNT}>{`-${showMoney(outcome.result.costs, outcome.result.currency)}`}</td></tr>
              </tbody>
            </table>
          </div>
        ) : (
          <p key="needs" className="text-slate-800" data-testid="fee-needs">{outcome.needs}</p>
        )}
      </section>

      <section aria-labelledby="fee-sources-title" className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5" data-testid="fee-sources">
        <h2 id="fee-sources-title" className="font-display text-2xl text-slate-900">Where the fees come from</h2>
        <p className="mt-2 text-sm text-slate-700" data-testid="fees-checked">{`Fees checked on ${longDate(FEES_CHECKED)}. Etsy can change its fees, and its own pages are what count.`}</p>
        <ul className="mt-3 space-y-3 text-[0.9375rem]">
          {FEES.map((fee) => (
            <li key={fee.id} data-testid={`fee-source-${fee.id}`}>
              <p className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium text-slate-900">{fee.name}</span>
                {fee.status === "to confirm" ? <span key="status" className="rounded border border-amber-200 bg-amber-50 px-1.5 text-xs leading-5 text-amber-900">To confirm</span> : null}
              </p>
              <p className="mt-0.5 text-sm leading-relaxed text-slate-600"><span>{`“${fee.quote}” `}</span><a href={fee.source.url} rel="noopener" className={LINK}>{fee.source.label}</a></p>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-sm leading-relaxed text-slate-700">
          <span>{`Not included: ${NOT_INCLUDED.join(", ")}.`}</span>
        </p>
        <SourceLinks sources={NOT_INCLUDED_SOURCES} />
        <p className="mt-3 text-sm text-slate-700">Figures confirmed against:</p>
        <SourceLinks sources={CHECKED_AGAINST} />
      </section>
    </div>
  );
}
