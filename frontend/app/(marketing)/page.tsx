import type { Metadata } from "next";
import Link from "next/link";
import { HeroDemo } from "@/components/marketing/HeroDemo";
import { Pipeline } from "@/components/marketing/Pipeline";
import { Closing, Faq, Features, YourData } from "@/components/marketing/Sections";

const TITLE = "Listyro — from a folder of mockups to review-ready drafts";
const DESCRIPTION =
  "A listing workflow and compliance assistant for print-on-demand and apparel sellers. Drafts copy the category, variations, pricing and size charts of your own listings, and nothing goes live until you approve or schedule it.";

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: { type: "website", url: "/", siteName: "Listyro", title: TITLE, description: DESCRIPTION, images: [{ url: "/og.png", width: 1200, height: 630, alt: "Listyro: a folder of mockups in, review-ready drafts out" }] },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION, images: ["/og.png"] },
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
              A folder of mockups in. <em className="text-brand-600">Review-ready</em> drafts out.
            </h1>
            <p className="mt-6 max-w-[33rem] text-[1.125rem] leading-relaxed text-slate-600">
              Listyro builds draft listings from your own designs, copying the category, variations, pricing, size
              charts and sections of listings you already sell. You read each one, change what you like, and nothing
              goes live until you approve or schedule it.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/request-invite" className="btn-primary px-5 py-3 text-[0.9375rem]">Request an invite</Link>
              <Link href="#how-it-works" className="btn-secondary px-5 py-3 text-[0.9375rem]">See how it works</Link>
            </div>
            <p className="mt-5 text-sm text-slate-500">Invite-only while in beta. No card, no payment.</p>
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
              Four steps, and you decide at every one.
            </h2>
            <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
              The slow part of a large catalog is not the idea, it is the hundredth listing form. Listyro does the
              form, from listings you have already set up, and leaves the judgement with you.
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
