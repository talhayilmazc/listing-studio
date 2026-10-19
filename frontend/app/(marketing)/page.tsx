import type { Metadata } from "next";
import Link from "next/link";
import { HeroDemo } from "@/components/marketing/HeroDemo";
import { Apart, Built, Closing, Faq, How, Safe } from "@/components/marketing/Sections";
import { HOME } from "@/lib/homeCopy";
import { TRADEMARK_NOTICE } from "@/lib/legal";

// Rendered per request, not served as a static file: "/" depends on who asks
// (middleware.ts sends a live session to the dashboard and clears a dead
// cookie), so it must never carry a header that lets a shared cache keep one
// visitor's answer for the next. Next marks static pages cacheable for a year
// whatever middleware sets; a dynamic page is sent as private, no-store.
export const dynamic = "force-dynamic";

const OG = { url: "/og-v3.png", width: 1200, height: 630, alt: HOME.ogAlt };

export const metadata: Metadata = {
  title: { absolute: HOME.title },
  description: HOME.description,
  alternates: { canonical: "/" },
  openGraph: { type: "website", url: "/", siteName: "Listyro", title: HOME.title, description: HOME.description, images: [OG] },
  twitter: { card: "summary_large_image", title: HOME.title, description: HOME.description, images: [OG.url] },
};

export default function Landing() {
  const hero = HOME.hero;
  return (
    <>
      <section className="relative overflow-hidden" aria-labelledby="hero-title">
        <div className="mx-auto grid w-full max-w-[1240px] grid-cols-1 gap-12 px-4 pb-20 pt-6 sm:px-6 sm:pt-14 lg:grid-cols-[minmax(0,0.96fr)_minmax(0,1.04fr)] lg:items-center lg:gap-10 lg:pb-28 lg:pt-20">
          <div className="min-w-0">
            <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">{hero.eyebrow}</p>
            <h1 id="hero-title" className="mt-3 font-display text-[clamp(2.1rem,5.4vw,3.75rem)] sm:mt-5 leading-[1.04] tracking-[-0.012em] text-slate-900">
              <span>{`${hero.heading} `}</span>
              <em className="block text-brand-600">{hero.headingAccent}</em>
            </h1>
            <p className="mt-4 max-w-[34rem] text-base leading-relaxed text-slate-600 sm:mt-6 sm:text-[1.125rem]">{hero.body}</p>
            <div className="mt-6 flex flex-wrap items-center gap-3 sm:mt-8">
              <Link href={hero.primary.href} className="btn-primary px-5 py-3 text-[0.9375rem]" data-testid="hero-cta">{hero.primary.label}</Link>
              <Link href={hero.secondary.href} className="btn-secondary px-5 py-3 text-[0.9375rem]">{hero.secondary.label}</Link>
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
          <div className="min-w-0 lg:-mr-6 xl:-mr-16">
            <HeroDemo />
          </div>
        </div>
      </section>

      <Apart />
      <Built />
      <How />
      <Safe />
      <Faq />
      <Closing />
    </>
  );
}
