import type { Metadata } from "next";
import { PlanCards } from "@/components/marketing/PlanCards";
import { Crumbs, JsonLd } from "@/components/marketing/Page";
import { INCLUDED, LISTING_LINE, PLANNED_NOTICE, PRICING_HEADING, PRICING_LINE, TRIAL } from "@/lib/plans";
import { PRIMARY_CTA } from "@/lib/marketing/cta";
import { metadataFor } from "@/lib/marketing/pages";
import { breadcrumbs, softwareApplication } from "@/lib/marketing/jsonld";

export const metadata: Metadata = metadataFor("/pricing");

// The launch plans are laid out from lib/plans.ts (components/marketing/PlanCards.tsx) and nowhere
// typed here. While billing is off they are prices for display, not an offer: there is no sign-up for
// one, no payment and no checkout anywhere on the site (lib/plans.test.ts).

export default function Pricing() {
  return (
    <>
      <JsonLd data={breadcrumbs("/pricing")} />
      <JsonLd data={softwareApplication()} />
      <div className="mk-wrap pb-20 pt-8 sm:pt-12">
        <Crumbs path="/pricing" />
        <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:items-end lg:gap-12">
          <div className="min-w-0">
            <h1 className="mk-h1 max-w-[20ch]">{PRICING_HEADING}</h1>
            <p className="mk-lead mt-4" data-testid="pricing-line">{PRICING_LINE}</p>
            <div className="mt-6">
              <a href={PRIMARY_CTA.href} className="mk-btn" data-testid="pricing-cta">{PRIMARY_CTA.label}</a>
            </div>
          </div>
          <p role="note" className="border-l-4 border-tag bg-fabric px-4 py-3 text-[1rem] font-semibold leading-relaxed text-ink" data-testid="planned-notice">
            {PLANNED_NOTICE}
          </p>
        </div>

        <div className="mt-10">
          <PlanCards />
        </div>

        <div className="mt-16 grid grid-cols-1 gap-10 border-t border-heather pt-10 md:grid-cols-2 md:gap-16" data-testid="included">
          <div className="min-w-0">
            <h2 className="mk-h2">What counts as a listing</h2>
            <p className="mk-body mt-4" data-testid="listing-line">{LISTING_LINE}</p>
            <p className="mk-body mt-4 font-semibold text-ink" data-testid="trial-line">{TRIAL.line}</p>
          </div>
          <div className="min-w-0">
            <h2 className="mk-h2">In every plan</h2>
            <ul className="mk-body mt-4 space-y-2">
              {INCLUDED.map((item) => (
                <li key={item} className="flex gap-3">
                  <span aria-hidden className="mt-[0.6rem] h-2 w-2 shrink-0 bg-ink" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="mt-16 grid grid-cols-1 gap-10 border-t border-heather pt-10 md:grid-cols-2 md:gap-16">
          <div className="min-w-0">
            <h2 className="mk-h3 !text-[1.375rem]">What you pay for</h2>
            <p className="mk-body mt-3">
              The work Listyro does for you: writing each listing from its design, making mockups on your own photos,
              checking for trademark problems, and the review, scheduling and reporting tools. Creating listings on
              Etsy and Etsy&apos;s own statistics cost nothing on Etsy, and they are not what you pay for. The
              reporting is your own profit, cost and ads report, not a copy of Etsy Stats.
            </p>
          </div>
          <div className="min-w-0">
            <h2 className="mk-h3 !text-[1.375rem]">What stays the same</h2>
            <p className="mk-body mt-3">
              Your shop, your listings and your Etsy fees stay between you and Etsy, exactly as they are now. Listyro
              never touches checkout or your buyers.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
