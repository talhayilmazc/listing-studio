import type { Metadata } from "next";
import { Check, HeroDemo } from "@/components/marketing/HeroDemo";
import { Closing, Faq, Grind, How, Pricing, Safe, ShopVersions, Strip, Voices } from "@/components/marketing/Sections";
import { JsonLd } from "@/components/marketing/Page";
import { HOME } from "@/lib/homeCopy";
import { faqPage, organization, softwareApplication } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

// A static page, so the edge can answer it for everyone. middleware.ts looks only at a request that
// carries the session cookie (a live session goes to the dashboard, a dead cookie is cleared), and
// any such request is marked private by lib/marketing/routes.mjs. This page knows nothing of who asks.
// The layout is docs/design/home-reference.dc.html (its values are the lx- classes in app/globals.css); the
// trademark notice is in the footer of this page, as of every page (components/marketing/Site.tsx).
export const metadata: Metadata = metadataFor("/");

export default function Landing() {
  const hero = HOME.hero;
  return (
    <div className="lx-ref" style={{ width: "100%", overflowX: "hidden" }}>
      <JsonLd data={organization()} />
      <JsonLd data={softwareApplication()} />
      <JsonLd data={faqPage([...HOME.faq.items])} />
      <section className="lx-hero-sec" aria-labelledby="hero-title">
        <div className="lx-wrap lx-hero">
          <div className="lx-hero-text">
            <span className="lx-badge" data-testid="hero-badge">
              <span className="lx-badge-dot">
                <Check />
              </span>
              <span>{hero.badge}</span>
            </span>
            <h1 id="hero-title" className="lx-h1">{hero.heading}</h1>
            <p className="lx-sub">{hero.sub}</p>
            <div className="lx-ctas">
              <a href={hero.primary.href} className="lx-btn lx-btn-ink lx-hero-cta" data-testid="hero-cta">{hero.primary.label}</a>
              {hero.video.href ? (
                <a key="video" href={hero.video.href} className="lx-video" data-testid="hero-video">
                  <span className="lx-video-dot">
                    <svg width="13" height="14" viewBox="0 0 13 14" aria-hidden="true"><path d="M2 1.5 L11.5 7 L2 12.5 Z" fill="#14162B" /></svg>
                  </span>
                  <span>{hero.video.label}</span>
                </a>
              ) : null}
            </div>
            <div className="lx-reassure">
              {hero.reassure.map((r) => (
                <span key={r}>
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="#1F8A4C" /><path d="M4.4 8.2 L6.9 10.5 L11.6 5.6" stroke="#FFFFFF" strokeWidth="1.9" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  <span>{r}</span>
                </span>
              ))}
            </div>
          </div>
          <HeroDemo />
        </div>
      </section>

      <Strip />
      <Grind />
      <How />
      <ShopVersions />
      <Safe />
      <Voices />
      <Pricing />
      <Faq />
      <Closing />
    </div>
  );
}
