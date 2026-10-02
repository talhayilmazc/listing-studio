import type { Metadata } from "next";
import Link from "next/link";
import { HeroDemo } from "@/components/marketing/HeroDemo";
import { Pipeline } from "@/components/marketing/Pipeline";
import { Closing, Faq, Features, YourData } from "@/components/marketing/Sections";

// Rendered per request, not served as a static file: "/" depends on who asks
// (middleware.ts sends a live session to the dashboard and clears a dead
// cookie), so it must never carry a header that lets a shared cache keep one
// visitor's answer for the next. Next marks static pages cacheable for a year
// whatever middleware sets; a dynamic page is sent as private, no-store.
export const dynamic = "force-dynamic";

const TITLE = "Listyro — drop a folder of mockups, get finished listings";
const DESCRIPTION =
  "For print-on-demand and apparel sellers: drop a folder of mockups and Listyro fills in every listing, with titles, tags, descriptions, categories, prices, size charts and sections, across all your shops. Every listing waits for your one-click approval or schedule.";

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: { type: "website", url: "/", siteName: "Listyro", title: TITLE, description: DESCRIPTION, images: [{ url: "/og-v2.png", width: 1200, height: 630, alt: "Listyro: drop a folder of mockups, get finished listings" }] },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION, images: ["/og-v2.png"] },
};

export default function Landing() {
  return (
    <>
      <section className="relative overflow-hidden">
        <div className="mx-auto grid w-full max-w-[1240px] grid-cols-1 gap-12 px-4 pb-20 pt-12 sm:px-6 sm:pt-16 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] lg:items-center lg:gap-10 lg:pb-28 lg:pt-20">
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-slate-500">
              <span className="rounded-full border border-brand-100 bg-brand-50 px-2.5 py-0.5 font-medium text-brand-700">Free during beta</span>
              <span>For print-on-demand and apparel sellers</span>
            </p>
            <h1 className="mt-6 font-display text-[clamp(2.9rem,7.2vw,5.4rem)] leading-[0.98] tracking-[-0.015em] text-slate-900">
              Drop a folder of mockups. <em className="text-brand-600">Get finished listings.</em>
            </h1>
            <p className="mt-6 max-w-[33rem] text-[1.125rem] leading-relaxed text-slate-600">
              Listyro does the listing work for you. Titles, tags and descriptions are written from each design. The
              category, prices, variations, size charts and shop section come from listings you already sell. A whole
              drop at a time, across all your shops, ready to go live.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/request-invite" className="btn-primary px-5 py-3 text-[0.9375rem]">Request an invite</Link>
              <Link href="#how-it-works" className="btn-secondary px-5 py-3 text-[0.9375rem]">See how it works</Link>
            </div>
            <p className="mt-5 flex items-start gap-2 text-sm text-slate-500">
              <svg aria-hidden viewBox="0 0 16 16" className="mt-[3px] h-3.5 w-3.5 shrink-0 text-emerald-600" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span>You stay in control: every listing waits for your one-click approval, or a time you pick, before it goes live.</span>
            </p>
          </div>
          <div className="min-w-0 lg:-mr-10 xl:-mr-24">
            <HeroDemo />
          </div>
        </div>
      </section>

      <section id="how-it-works" className="scroll-mt-20 border-t border-slate-200 bg-white">
        <div className="mx-auto w-full max-w-[1240px] px-4 py-20 sm:px-6 lg:py-28">
          <div className="max-w-[44rem]">
            <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">How it works</p>
            <h2 className="mt-3 font-display text-[clamp(2.2rem,4.6vw,3.6rem)] leading-[1.04] text-slate-900">
              From folder to live listings in four steps.
            </h2>
            <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
              You make the designs. Listyro does the listing work: every field, for every design, in every shop you
              sell from.
            </p>
          </div>
          <div className="mt-14 lg:mt-6">
            <Pipeline />
          </div>
        </div>
      </section>

      <Features />
      <YourData />
      <Faq />
      <Closing />
    </>
  );
}
