import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Pricing",
  description: "Listyro is free during its beta. Paid plans start only after the beta ends, and nothing is charged until then.",
  alternates: { canonical: "/pricing" },
};

// No prices are shown for the planned plans because none are set; nothing is
// charged, and there is no payment flow, until the beta ends.
const PLANNED: { name: string; for: string; points: string[] }[] = [
  {
    name: "Solo",
    for: "One shop, a steady stream of new designs.",
    points: ["One shop", "Profiles from your own listings", "Review, approve and schedule", "Compliance checks"],
  },
  {
    name: "Studio",
    for: "A few shops and a larger monthly catalog.",
    points: ["Several shops", "Send one listing to several shops", "Profit analytics", "Everything in Solo"],
  },
  {
    name: "Catalog",
    for: "High volume across many shops.",
    points: ["Up to eight shops", "The largest monthly allowance", "Priority support", "Everything in Studio"],
  },
];

export default function Pricing() {
  return (
    <div className="mx-auto w-full max-w-[1240px] px-4 pb-24 pt-12 sm:px-6 sm:pt-16">
      <div className="max-w-[46rem]">
        <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">Pricing</p>
        <h1 className="mt-3 font-display text-[clamp(2.6rem,6vw,4.25rem)] leading-[1.02] text-slate-900">
          Free during the beta.
        </h1>
        <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
          Everything is included while Listyro is in beta, and there is nothing to pay or enter. Paid plans start only
          after the beta, and every beta seller will be told well before.
        </p>
      </div>

      <div className="mt-14 grid grid-cols-1 gap-5 lg:grid-cols-4">
        <div className="min-w-0 rounded-2xl border-2 border-brand-600 bg-white p-6">
          <p className="flex items-center justify-between gap-2">
            <span className="font-display text-3xl text-slate-900">Beta</span>
            <span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-medium text-white">Now</span>
          </p>
          <p className="mt-4 font-display text-5xl leading-none text-slate-900">Free</p>
          <p className="mt-2 text-sm text-slate-500">Invite-only. No card, no payment.</p>
          <ul className="mt-6 space-y-2 text-sm text-slate-600">
            {["Every feature", "Up to eight shops", "A monthly allowance of listings, set per seller", "Help from the people who build it"].map((p) => (
              <li key={p} className="flex gap-2.5">
                <span aria-hidden className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-brand-600" />
                <span>{p}</span>
              </li>
            ))}
          </ul>
          <Link href="/request-invite" className="btn-primary mt-8 w-full px-5 py-3">Request an invite</Link>
        </div>
        {PLANNED.map((plan) => (
          <div key={plan.name} className="min-w-0 rounded-2xl border border-slate-200 bg-white p-6">
            <p className="flex items-center justify-between gap-2">
              <span className="font-display text-3xl text-slate-900">{plan.name}</span>
              <span className="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-500">After beta</span>
            </p>
            <p className="mt-4 text-sm font-medium text-slate-500">Price to be announced</p>
            <p className="mt-2 text-sm text-slate-600">{plan.for}</p>
            <ul className="mt-6 space-y-2 text-sm text-slate-600">
              {plan.points.map((p) => (
                <li key={p} className="flex gap-2.5">
                  <span aria-hidden className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-slate-300" />
                  <span>{p}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="mt-16 grid grid-cols-1 gap-10 border-t border-slate-200 pt-12 md:grid-cols-2 md:gap-16">
        <div className="min-w-0">
          <h2 className="font-display text-3xl text-slate-900">What a plan will pay for</h2>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            The work Listyro does itself: reading each design, writing the text, preparing the images, checking for
            trademark and policy problems, and the review, scheduling and analytics workflow. Connecting your shop and
            the Etsy features you already have are not something we charge for.
          </p>
        </div>
        <div className="min-w-0">
          <h2 className="font-display text-3xl text-slate-900">What it will not change</h2>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            Etsy&rsquo;s own listing, transaction and advertising fees are between you and Etsy, exactly as they are
            now. Listyro never charges them, never pays them, and never takes payments from buyers.
          </p>
        </div>
      </div>
    </div>
  );
}
