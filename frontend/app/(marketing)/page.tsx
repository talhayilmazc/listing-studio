import type { Metadata } from "next";
import { HeroDemo } from "@/components/marketing/HeroDemo";
import { Closing, Faq, How, PricingTeaser, Testimonials, Why } from "@/components/marketing/Sections";
import { JsonLd } from "@/components/marketing/Page";
import { HOME } from "@/lib/homeCopy";
import { faqPage, organization, softwareApplication } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

// A static page, so the edge can answer it for everyone. middleware.ts looks only at a request that
// carries the session cookie (a live session goes to the dashboard, a dead cookie is cleared), and
// any such request is marked private by lib/marketing/routes.mjs. This page knows nothing of who asks.
// The trademark notice is in the footer of this page, as of every page (components/marketing/Site.tsx).
export const metadata: Metadata = metadataFor("/");

export default function Landing() {
  const hero = HOME.hero;
  return (
    <>
      <JsonLd data={organization()} />
      <JsonLd data={softwareApplication()} />
      <JsonLd data={faqPage([...HOME.faq.items])} />
      <section aria-labelledby="hero-title">
        <div className="mk-wrap grid grid-cols-1 gap-10 pb-16 pt-10 sm:pt-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,34rem)] lg:items-center lg:gap-14 lg:pb-24 lg:pt-20">
          <div className="min-w-0">
            <h1 id="hero-title" className="mk-display max-w-[21ch]">{hero.heading}</h1>
            <p className="mk-lead mt-5 sm:mt-6">{hero.sub}</p>
            <div className="mt-7 flex flex-wrap items-center gap-x-6 gap-y-4 sm:mt-8">
              <a href={hero.primary.href} className="mk-btn" data-testid="hero-cta">{hero.primary.label}</a>
              <a href={hero.secondary.href} className="mk-link">{hero.secondary.label}</a>
            </div>
            <p className="mk-small mt-5" data-testid="hero-note">{hero.note}</p>
          </div>
          <HeroDemo />
        </div>
      </section>

      <How />
      <Why />
      <Testimonials />
      <PricingTeaser />
      <Faq />
      <Closing />
    </>
  );
}
