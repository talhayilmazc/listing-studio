"use client";

import Link from "next/link";
import { useState } from "react";
import { CUSTOM, PLANS, YEARLY_NOTE, listingsFact, priceFact, schedulingFact, shopsFact, yearlyPerMonthFact, yearlyPriceFact } from "@/lib/plans";

/**
 * The planned plans, with a switch between what one would cost paid monthly and
 * paid yearly. The switch only changes what is shown: it is two plain buttons
 * and a piece of state, not a form, and nothing on these cards can be bought.
 * Every figure comes from lib/plans.ts.
 */

const DOT = "mt-[0.55rem] h-1 w-1 shrink-0 rounded-full";

export function PlanCards() {
  const [yearly, setYearly] = useState(false);
  const choice = (on: boolean) =>
    "min-h-[2.75rem] rounded-full px-5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 " +
    (on ? "bg-slate-900 text-white" : "text-slate-600 hover:text-slate-900");

  return (
    <div>
      <div role="group" aria-label="Show prices paid monthly or yearly" className="inline-flex rounded-full border border-slate-200 bg-white p-1" data-testid="billing-switch">
        <button type="button" aria-pressed={!yearly} onClick={() => setYearly(false)} className={choice(!yearly)} data-testid="billing-monthly">
          Monthly
        </button>
        <button type="button" aria-pressed={yearly} onClick={() => setYearly(true)} className={choice(yearly)} data-testid="billing-yearly">
          Yearly
        </button>
      </div>

      <ul className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3" data-testid="plans">
        {PLANS.map((plan) => (
          <li key={plan.name} className="flex min-w-0 flex-col rounded-2xl border border-slate-200 bg-white p-6" data-testid="plan">
            <p className="flex items-center justify-between gap-2">
              <span className="font-display text-3xl text-slate-900">{plan.name}</span>
              <span className="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-500">Planned</span>
            </p>
            <p className="mt-4 flex items-baseline gap-1.5">
              <span className="font-display text-5xl leading-none text-slate-900" translate="no" data-testid="plan-price">
                {yearly ? yearlyPriceFact(plan) : priceFact(plan)}
              </span>
              <span className="text-sm text-slate-500">{yearly ? "a year, planned" : "a month, planned"}</span>
            </p>
            {/* Two lines kept in both views, so the cards do not change height with the switch. */}
            <p className="mt-2 min-h-[2.8rem] text-sm leading-snug text-slate-600" translate="no" data-testid="plan-yearly-note">
              <span className="block font-medium text-slate-800">{yearly ? YEARLY_NOTE : ""}</span>
              <span className="block">{yearly ? yearlyPerMonthFact(plan) : ""}</span>
            </p>
            <ul className="mt-4 space-y-2 text-sm text-slate-700">
              {[listingsFact(plan), shopsFact(plan), schedulingFact(plan)].map((fact) => (
                <li key={fact} className="flex gap-2.5">
                  <span aria-hidden className={DOT + " bg-slate-400"} />
                  <span>{fact}</span>
                </li>
              ))}
              <li className="flex gap-2.5 text-slate-500">
                <span aria-hidden className={DOT + " bg-slate-300"} />
                <span>Everything else included</span>
              </li>
            </ul>
          </li>
        ))}
        <li className="flex min-w-0 flex-col rounded-2xl border border-dashed border-slate-300 bg-stone-50 p-6 sm:col-span-2" data-testid="plan-custom">
          <p className="font-display text-3xl text-slate-900">{CUSTOM.name}</p>
          <p className="mt-4 max-w-[34rem] text-[0.9375rem] leading-relaxed text-slate-600">{CUSTOM.body}</p>
          <div className="mt-auto pt-8">
            <Link href={CUSTOM.cta.href} className="btn-secondary px-5 py-3" data-testid="custom-cta">{CUSTOM.cta.label}</Link>
          </div>
        </li>
      </ul>
    </div>
  );
}
