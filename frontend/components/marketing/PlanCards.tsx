"use client";

import { useState, type ReactNode } from "react";
import { PRIMARY_CTA } from "@/lib/marketing/cta";
import {
  EXTRA_PACK, PLANS, SPECIAL, YEARLY_LABEL, YEARLY_NOTE, cardListingsFact, extraPackFact, perListingFact, priceFact, shopsFact,
  yearlyPerMonthFact, yearlyPriceFact,
} from "@/lib/plans";

/**
 * The launch plans as the home page reference draws them (docs/design/home-reference.dc.html): a heading
 * row with the Monthly / Yearly switch, then five cards, the highlighted plan in ink (the lx-plan classes in
 * app/globals.css). The switch only changes what is shown: two plain buttons and a piece of state, not a form,
 * and nothing on these cards can be bought. Every figure comes from lib/plans.ts, the cost of one listing
 * included.
 */
export function PlanCards({ children, extras = false }: { children?: ReactNode; extras?: boolean }) {
  const [yearly, setYearly] = useState(false);

  return (
    <div className="lx-stack-44">
      <div className="lx-plan-top">
        {children}
        <span role="group" aria-label="Show prices paid monthly or yearly" className="lx-switch" data-testid="billing-switch">
          <button type="button" aria-pressed={!yearly} onClick={() => setYearly(false)} data-testid="billing-monthly">Monthly</button>
          <button type="button" aria-pressed={yearly} onClick={() => setYearly(true)} data-testid="billing-yearly">{YEARLY_LABEL}</button>
        </span>
      </div>

      <div className="lx-plans" data-testid="plans">
        {PLANS.map((plan) => (
          <div key={plan.name} className={plan.highlight ? "lx-plan lx-plan-hi lx-dark" : "lx-plan"} data-testid={plan.highlight ? "plan-highlight" : "plan"}>
            <span className="lx-plan-name">{plan.name}</span>
            <span className="lx-plan-price" translate="no">
              <span className="lx-plan-amount" data-testid="plan-price">{yearly ? yearlyPriceFact(plan) : priceFact(plan)}</span>
              <span className="lx-plan-per">{yearly ? "/yr" : "/mo"}</span>
            </span>
            <span className="lx-plan-unit" translate="no" data-testid="plan-per-listing">{perListingFact(plan, yearly)}</span>
            <span className="lx-plan-facts">
              <span>{cardListingsFact(plan)}</span>
              <span>{shopsFact(plan)}</span>
            </span>
            <a href={PRIMARY_CTA.href} className={"lx-btn lx-plan-cta " + (plan.highlight ? "lx-btn-tag" : "lx-btn-soft")}>{PRIMARY_CTA.label}</a>
          </div>
        ))}
        <div className="lx-plan lx-plan-special" data-testid="plan-special">
          <span className="lx-plan-name">{SPECIAL.name}</span>
          <span className="lx-plan-price">
            <span className="lx-plan-amount lx-plan-custom">{SPECIAL.price}</span>
          </span>
          <span className="lx-plan-unit">{SPECIAL.unit}</span>
          <span className="lx-plan-facts">
            <span>{SPECIAL.listings}</span>
            <span>{SPECIAL.shops}</span>
          </span>
          <a href={SPECIAL.cta.href} className="lx-btn lx-btn-white lx-plan-cta" data-testid="special-cta">{SPECIAL.cta.label}</a>
        </div>
      </div>

      {extras ? (
        <div key="extras" className="lx-two">
          <div className="mk-card" data-testid="extra-pack">
            <p className="mk-h3" translate="no">{extraPackFact()}</p>
            <p className="mk-small" style={{ marginTop: 6 }}>{EXTRA_PACK.line}</p>
          </div>
          <div className="mk-card" data-testid="plan-yearly">
            <p className="mk-h3">{YEARLY_NOTE}</p>
            <p className="mk-small" style={{ marginTop: 6 }} translate="no">
              {PLANS.map((plan) => `${plan.name} ${yearlyPerMonthFact(plan)}`).join(" · ")}
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
