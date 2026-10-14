import type { Metadata } from "next";
import Link from "next/link";
import { PlanCards } from "@/components/marketing/PlanCards";
import { INCLUDED, LISTING_LINE, PLANNED_NOTICE, PRICING_HEADING, PRICING_LINE } from "@/lib/plans";

export const metadata: Metadata = {
  title: "Pricing",
  description: "Listyro is in invite-only early access and no payment is taken today. The plans planned for after launch are shown; prices may change before launch.",
  alternates: { canonical: "/pricing" },
};

// The planned plans are laid out from lib/plans.ts (components/marketing/PlanCards.tsx)
// and nowhere typed here. They are plans, not an offer: there is no sign-up for one,
// no payment and no checkout anywhere on the site (lib/plans.test.ts).

const DOT = "mt-[0.55rem] h-1 w-1 shrink-0 rounded-full";

export default function Pricing() {
  return (
    <div className="mx-auto w-full max-w-[1240px] px-4 pb-24 pt-12 sm:px-6 sm:pt-16">
      <div className="max-w-[46rem]">
        <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">Pricing</p>
        <h1 className="mt-3 font-display text-[clamp(2.4rem,5.4vw,3.9rem)] leading-[1.04] text-slate-900">{PRICING_HEADING}</h1>
        <div className="mt-7">
          <Link href="/request-invite" className="btn-primary px-5 py-3 text-[0.9375rem]" data-testid="pricing-cta">Request an invite</Link>
        </div>
        <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600" data-testid="pricing-line">{PRICING_LINE}</p>
      </div>

      <p role="note" className="mb-6 mt-12 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[0.9375rem] font-medium leading-relaxed text-amber-700" data-testid="planned-notice">
        {PLANNED_NOTICE}
      </p>

      <PlanCards />

      <div className="mt-12 grid grid-cols-1 gap-10 rounded-3xl bg-white p-6 sm:p-10 md:grid-cols-2 md:gap-16" data-testid="included">
        <div className="min-w-0">
          <h2 className="font-display text-3xl text-slate-900">In every plan</h2>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            The plans differ in how many listings are written a month, in how many shops, and in whether
            scheduling is included or an add-on. Everything else is the same in all of them.
          </p>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600" data-testid="listing-line">{LISTING_LINE}</p>
        </div>
        <ul className="min-w-0 space-y-2.5 text-[0.9375rem] text-slate-700">
          {INCLUDED.map((item) => (
            <li key={item} className="flex gap-2.5">
              <span aria-hidden className={DOT + " bg-brand-600"} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-16 grid grid-cols-1 gap-10 border-t border-slate-200 pt-12 md:grid-cols-2 md:gap-16">
        <div className="min-w-0">
          <h2 className="font-display text-3xl text-slate-900">What the plans will cover</h2>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            The work Listyro does for you: suggesting each listing&apos;s text from its design, preparing the photos,
            checking for trademark problems, and the review, scheduling and reporting tools. Creating listings on
            Etsy and Etsy&apos;s own statistics cost nothing on Etsy, and they are not what you would pay for.
            The reporting is your own profit, cost and ads report, not a copy of Etsy Stats.
          </p>
        </div>
        <div className="min-w-0">
          <h2 className="font-display text-3xl text-slate-900">What stays the same</h2>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            Your shop, your listings and your Etsy fees stay between you and Etsy, exactly as they are now. Listyro
            never touches checkout or your buyers.
          </p>
        </div>
      </div>
    </div>
  );
}
