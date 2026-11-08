"use client";

import { useState, type ReactNode } from "react";
import { PRIMARY_CTA } from "@/lib/marketing/cta";
import {
  EXTRA_PACK, PLANS, SPECIAL, YEARLY_LABEL, YEARLY_NOTE, cardListingsFact, extraPackFact, perListingFact, priceFact, shopsFact,
  yearlyPerMonthFact, yearlyPriceFact,
} from "@/lib/plans";

/**
 * The launch plans as the home page reference draws them (docs/design/home-reference.dc.html): a heading
 * row with the Monthly / Yearly switch, then five cards, the highlighted plan in ink. The switch only changes
 * what is shown: two plain buttons and a piece of state, not a form, and nothing on these cards can be
 * bought. Every figure comes from lib/plans.ts, the cost of one listing included.
 */

const CARD = { bg: "#FFFFFF", fg: "#14162B", border: "1px solid #E3E6EE", shadow: "none", btn: "lx-btn-soft" };
const HIGHLIGHT = { bg: "#14162B", fg: "#FFFFFF", border: "1px solid #14162B", shadow: "0 30px 60px rgba(20,22,43,0.25)", btn: "lx-btn-tag" };

export function PlanCards({ children, extras = false }: { children?: ReactNode; extras?: boolean }) {
  const [yearly, setYearly] = useState(false);
  const half = (on: boolean): React.CSSProperties => ({
    padding: "10px 18px",
    borderRadius: 999,
    minHeight: 44,
    border: 0,
    font: "inherit",
    cursor: "pointer",
    background: on ? "#14162B" : "transparent",
    color: on ? "#FFFFFF" : "#14162B",
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 44 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "20px 56px", alignItems: "flex-end", justifyContent: "space-between" }}>
        {children}
        <span role="group" aria-label="Show prices paid monthly or yearly" style={{ display: "inline-flex", gap: 4, padding: 5, borderRadius: 999, background: "#F1F2F7", fontSize: 15, fontWeight: 800 }} data-testid="billing-switch">
          <button type="button" aria-pressed={!yearly} onClick={() => setYearly(false)} style={half(!yearly)} data-testid="billing-monthly">Monthly</button>
          <button type="button" aria-pressed={yearly} onClick={() => setYearly(true)} style={half(yearly)} data-testid="billing-yearly">{YEARLY_LABEL}</button>
        </span>
      </div>

      <div className="lx-plans" data-testid="plans">
        {PLANS.map((plan) => {
          const look = plan.highlight ? HIGHLIGHT : CARD;
          return (
            <div key={plan.name} style={{ borderRadius: 24, padding: "28px 22px", background: look.bg, color: look.fg, border: look.border, boxShadow: look.shadow, display: "flex", flexDirection: "column", gap: 14 }} className={plan.highlight ? "lx-dark" : undefined} data-testid={plan.highlight ? "plan-highlight" : "plan"}>
              <span style={{ fontSize: 17, fontWeight: 850 }}>{plan.name}</span>
              <span style={{ display: "flex", alignItems: "baseline", gap: 4 }} translate="no">
                <span style={{ fontSize: 46, fontWeight: 900, fontStretch: "110%", letterSpacing: -1 }} data-testid="plan-price">{yearly ? yearlyPriceFact(plan) : priceFact(plan)}</span>
                <span style={{ fontSize: 15, opacity: 0.72 }}>{yearly ? "/yr" : "/mo"}</span>
              </span>
              <span style={{ fontSize: 13, fontWeight: 700, opacity: 0.72 }} translate="no" data-testid="plan-per-listing">{perListingFact(plan, yearly)}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: 7, fontSize: 15, fontWeight: 650 }}>
                <span>{cardListingsFact(plan)}</span>
                <span>{shopsFact(plan)}</span>
              </span>
              <a href={PRIMARY_CTA.href} className={"lx-btn " + look.btn} style={{ marginTop: "auto", textAlign: "center", fontSize: 16, fontWeight: 850, padding: "14px 16px" }}>{PRIMARY_CTA.label}</a>
            </div>
          );
        })}
        <div style={{ borderRadius: 24, padding: "28px 22px", background: "#F5F6FA", color: "#14162B", border: "1px solid #E6E8EF", display: "flex", flexDirection: "column", gap: 14 }} data-testid="plan-special">
          <span style={{ fontSize: 17, fontWeight: 850 }}>{SPECIAL.name}</span>
          <span style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
            {/* 42 px, not the drawn 46: at 46 the word runs past the card at five columns (in the reference too). */}
            <span style={{ fontSize: 42, fontWeight: 900, fontStretch: "110%", letterSpacing: -1 }}>{SPECIAL.price}</span>
          </span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "#4B4F6B" }}>{SPECIAL.unit}</span>
          <span style={{ display: "flex", flexDirection: "column", gap: 7, fontSize: 15, fontWeight: 650 }}>
            <span>{SPECIAL.listings}</span>
            <span>{SPECIAL.shops}</span>
          </span>
          <a href={SPECIAL.cta.href} className="lx-btn lx-btn-white" style={{ marginTop: "auto", textAlign: "center", fontSize: 16, fontWeight: 850, padding: "14px 16px" }} data-testid="special-cta">{SPECIAL.cta.label}</a>
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
