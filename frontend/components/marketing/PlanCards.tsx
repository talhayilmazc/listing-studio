"use client";

import { useState } from "react";
import {
  EXTRA_PACK, PLANS, SPECIAL, YEARLY_NOTE, extraPackFact, listingsFact, priceFact, shopsFact, yearlyPerMonthFact, yearlyPriceFact,
} from "@/lib/plans";

/**
 * The launch plans as hang tags, with a switch between what one costs paid monthly and paid yearly. The
 * switch only changes what is shown: two plain buttons and a piece of state, not a form, and nothing on
 * these tags can be bought. Every figure comes from lib/plans.ts.
 */

export function PlanCards() {
  const [yearly, setYearly] = useState(false);
  const choice = (on: boolean) =>
    "min-h-[2.75rem] px-5 text-[0.9375rem] font-semibold transition-colors " + (on ? "bg-tag text-ink" : "bg-white text-ink hover:bg-fabric");

  return (
    <div>
      <div role="group" aria-label="Show prices paid monthly or yearly" className="inline-flex border-[1.5px] border-ink" data-testid="billing-switch">
        <button type="button" aria-pressed={!yearly} onClick={() => setYearly(false)} className={choice(!yearly)} data-testid="billing-monthly">
          Monthly
        </button>
        <button type="button" aria-pressed={yearly} onClick={() => setYearly(true)} className={choice(yearly) + " border-l-[1.5px] border-ink"} data-testid="billing-yearly">
          Yearly
        </button>
      </div>

      <ul className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="plans">
        {PLANS.map((plan) => (
          <li key={plan.name} className={"mk-tag flex min-w-0 flex-col px-5 pb-6 pt-10 " + (plan.highlight ? "bg-tag" : "bg-fabric")} data-testid="plan">
            <p className="flex items-center justify-between gap-2">
              <span className="text-[1.25rem] font-bold text-ink">{plan.name}</span>
              {plan.highlight ? <span key="pick" className="border border-ink px-2 py-0.5 text-[0.8125rem] font-semibold text-ink" data-testid="plan-highlight">A good place to start</span> : null}
            </p>
            <p className="mt-4 flex items-baseline gap-1.5 text-ink">
              <span className="text-[2.75rem] font-extrabold leading-none [font-stretch:125%]" translate="no" data-testid="plan-price">
                {yearly ? yearlyPriceFact(plan) : priceFact(plan)}
              </span>
              <span className="text-[0.9375rem] font-medium">{yearly ? "a year" : "a month"}</span>
            </p>
            {/* Two lines kept in both views, so the tags do not change height with the switch. */}
            <p className="mt-2 min-h-[2.8rem] text-[0.9375rem] leading-snug text-ink" translate="no" data-testid="plan-yearly-note">
              <span className="block font-semibold">{yearly ? YEARLY_NOTE : ""}</span>
              <span className="block">{yearly ? yearlyPerMonthFact(plan) : ""}</span>
            </p>
            <ul className="mt-3 space-y-1.5 border-t border-ink/20 pt-4 text-[0.9375rem] text-ink">
              <li className="font-semibold">{listingsFact(plan)}</li>
              <li>{shopsFact(plan)}</li>
              <li>Every feature included</li>
            </ul>
          </li>
        ))}
      </ul>

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="border border-heather bg-white p-5 sm:p-6" data-testid="extra-pack">
          <p className="text-[1.0625rem] font-bold text-ink" translate="no">{extraPackFact()}</p>
          <p className="mk-small mt-1">{EXTRA_PACK.line}</p>
        </div>
        <div className="flex flex-col items-start gap-4 border border-heather bg-white p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6" data-testid="plan-special">
          <div className="min-w-0">
            <p className="text-[1.0625rem] font-bold text-ink">{SPECIAL.name}</p>
            <p className="mk-small mt-1">{SPECIAL.body}</p>
          </div>
          <a href={SPECIAL.cta.href} className="mk-btn-line shrink-0" data-testid="special-cta">{SPECIAL.cta.label}</a>
        </div>
      </div>
    </div>
  );
}
