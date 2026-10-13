import type { Metadata } from "next";
import Link from "next/link";
import { CUSTOM, INCLUDED, LISTING_LINE, PLANNED_NOTICE, PLANS, listingsFact, priceFact, schedulingFact, shopsFact } from "@/lib/plans";

export const metadata: Metadata = {
  title: "Pricing",
  description: "Listyro is free during early access. The plans planned for after launch are shown; no payment is taken today, and prices may change before launch.",
  alternates: { canonical: "/pricing" },
};

// The planned plans are laid out from lib/plans.ts and nowhere typed here. They
// are plans, not an offer: there is no sign-up for one, no payment and no
// checkout anywhere on the site (lib/plans.test.ts).

const DOT = "mt-[0.55rem] h-1 w-1 shrink-0 rounded-full";

export default function Pricing() {
  return (
    <div className="mx-auto w-full max-w-[1240px] px-4 pb-24 pt-12 sm:px-6 sm:pt-16">
      <div className="max-w-[46rem]">
        <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">Pricing</p>
        <h1 className="mt-3 font-display text-[clamp(2.6rem,6vw,4.25rem)] leading-[1.02] text-slate-900">
          Free during early access.
        </h1>
        <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
          Everything is included during early access and no payment of any kind is taken; there is no checkout
          on this site. Pricing applies only after launch and will be announced well before, and early-access
          sellers will hear first.
        </p>
      </div>

      <p role="note" className="mt-10 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[0.9375rem] font-medium leading-relaxed text-amber-700" data-testid="planned-notice">
        {PLANNED_NOTICE}
      </p>

      <ul className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3" data-testid="plans">
        <li className="flex min-w-0 flex-col rounded-2xl border-2 border-brand-600 bg-white p-6" data-testid="plan-early-access">
          <p className="flex items-center justify-between gap-2">
            <span className="font-display text-3xl text-slate-900">Early access</span>
            <span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-medium text-white">Now</span>
          </p>
          <p className="mt-4 font-display text-5xl leading-none text-slate-900">Free</p>
          <p className="mt-2 text-sm text-slate-500">Invite-only.</p>
          <ul className="mt-6 space-y-2 text-sm text-slate-600">
            {["Every feature", "Several shops; the number is set per account", "A monthly allowance of listings, set per seller", "Help from the people who build it"].map((p) => (
              <li key={p} className="flex gap-2.5">
                <span aria-hidden className={DOT + " bg-brand-600"} />
                <span>{p}</span>
              </li>
            ))}
          </ul>
          <div className="mt-auto pt-8">
            <Link href="/request-invite" className="btn-primary w-full px-5 py-3">Request an invite</Link>
          </div>
        </li>
        {PLANS.map((plan) => (
          <li key={plan.name} className="flex min-w-0 flex-col rounded-2xl border border-slate-200 bg-white p-6" data-testid="plan">
            <p className="flex items-center justify-between gap-2">
              <span className="font-display text-3xl text-slate-900">{plan.name}</span>
              <span className="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-500">Planned</span>
            </p>
            <p className="mt-4 flex items-baseline gap-1.5">
              <span className="font-display text-5xl leading-none text-slate-900" translate="no">{priceFact(plan)}</span>
              <span className="text-sm text-slate-500">a month, planned</span>
            </p>
            <ul className="mt-6 space-y-2 text-sm text-slate-700">
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
        <li className="flex min-w-0 flex-col rounded-2xl border border-dashed border-slate-300 bg-stone-50 p-6" data-testid="plan-custom">
          <p className="font-display text-3xl text-slate-900">{CUSTOM.name}</p>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">{CUSTOM.body}</p>
          <div className="mt-auto pt-8">
            <Link href={CUSTOM.cta.href} className="btn-secondary w-full px-5 py-3" data-testid="custom-cta">{CUSTOM.cta.label}</Link>
          </div>
        </li>
      </ul>

      <div className="mt-12 grid grid-cols-1 gap-10 rounded-3xl bg-white p-6 sm:p-10 md:grid-cols-2 md:gap-16" data-testid="included">
        <div className="min-w-0">
          <h2 className="font-display text-3xl text-slate-900">In every plan</h2>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            The plans differ in how many new listings are prepared a month, in how many shops, and in whether a draft
            can be scheduled. Everything else is the same in all of them.
          </p>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">{LISTING_LINE}</p>
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
            Etsy and Etsy&apos;s own statistics stay free, as Etsy offers them: they are not what you would pay for.
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
