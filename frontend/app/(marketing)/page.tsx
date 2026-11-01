import type { Metadata } from "next";
import { Apart, Closing, Faq, How, PricingTeaser, Testimonials, VideoSlot, Who } from "@/components/marketing/Sections";
import { JsonLd } from "@/components/marketing/Page";
import { HOME } from "@/lib/homeCopy";
import { faqPage, organization, softwareApplication } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";
import { TRADEMARK_NOTICE } from "@/lib/legal";

// A static page, so the edge can answer it for everyone. middleware.ts looks only at a request that
// carries the session cookie (a live session goes to the dashboard, a dead cookie is cleared), and
// any such request is marked private by lib/marketing/routes.mjs. This page knows nothing of who asks.
export const metadata: Metadata = metadataFor("/");

export default function Landing() {
  const hero = HOME.hero;
  return (
    <>
      <JsonLd data={organization()} />
      <JsonLd data={softwareApplication()} />
      <JsonLd data={faqPage([...HOME.faq.items])} />
      <section className="relative overflow-hidden" aria-labelledby="hero-title">
        <div className="mx-auto grid w-full max-w-[1240px] grid-cols-1 gap-10 px-4 pb-16 pt-6 sm:px-6 sm:pt-14 lg:grid-cols-[minmax(0,0.96fr)_minmax(0,1.04fr)] lg:items-center lg:gap-10 lg:pb-24 lg:pt-20">
          <div className="min-w-0">
            <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">{hero.eyebrow}</p>
            <h1 id="hero-title" className="mt-3 font-display text-[clamp(2.1rem,5.4vw,3.75rem)] sm:mt-5 leading-[1.04] tracking-[-0.012em] text-slate-900">
              <span>{`${hero.heading} `}</span>
              <em className="block text-brand-600">{hero.headingAccent}</em>
            </h1>
            <div className="mt-6 flex flex-wrap items-center gap-3 sm:mt-8">
              <a href={hero.primary.href} className="btn-primary px-5 py-3 text-[0.9375rem]" data-testid="hero-cta">{hero.primary.label}</a>
              <a href={hero.secondary.href} className="btn-secondary px-5 py-3 text-[0.9375rem]">{hero.secondary.label}</a>
            </div>
            <p className="mt-3 flex items-start gap-2 text-sm text-slate-600 sm:mt-5" data-testid="hero-note">
              <span aria-hidden className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-600" />
              <span>{hero.note}</span>
            </p>
            {/* Etsy's Commercial Access review asks for the notice in a prominent place on the
                home page, not only in the footer: here, beside the first call to action. */}
            <p className="mt-4 max-w-[34rem] rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-sm leading-relaxed text-slate-700 sm:mt-6 sm:px-4 sm:py-3"
              data-testid="home-trademark-notice">{TRADEMARK_NOTICE}</p>
          </div>
          <div className="min-w-0">
            <VideoSlot />
          </div>
        </div>
      </section>

      <How />
      <Apart />
      <Who />
      <Testimonials />
      <PricingTeaser />
      <Faq />
      <Closing />
    </>
  );
}
