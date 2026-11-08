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
// The layout is docs/design/home-reference.dc.html; the trademark notice is in the footer of this page,
// as of every page (components/marketing/Site.tsx).
export const metadata: Metadata = metadataFor("/");

const HERO_GLOW =
  "radial-gradient(700px 480px at 82% 20%, rgba(255,210,74,0.30), rgba(255,210,74,0) 70%), radial-gradient(700px 520px at 98% 70%, rgba(150,120,255,0.20), rgba(150,120,255,0) 70%), radial-gradient(600px 400px at 0% 0%, rgba(120,190,255,0.14), rgba(120,190,255,0) 70%), #FFFFFF";

export default function Landing() {
  const hero = HOME.hero;
  return (
    <div className="lx-ref" style={{ width: "100%", overflowX: "hidden" }}>
      <JsonLd data={organization()} />
      <JsonLd data={softwareApplication()} />
      <JsonLd data={faqPage([...HOME.faq.items])} />
      <section style={{ position: "relative", padding: "40px 0 120px", background: HERO_GLOW }} aria-labelledby="hero-title">
        <div className="lx-wrap lx-hero">
          <div style={{ display: "flex", flexDirection: "column", gap: 26, minWidth: 0 }}>
            <span style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 10, padding: "7px 14px 7px 8px", borderRadius: 999, background: "#FFFFFF", border: "1px solid #E6E8EF", boxShadow: "0 2px 10px rgba(20,22,43,0.06)", fontSize: 14, fontWeight: 700 }} data-testid="hero-badge">
              <span style={{ display: "inline-flex", width: 22, height: 22, borderRadius: 99, background: "#E9F6EE", alignItems: "center", justifyContent: "center" }}>
                <Check />
              </span>
              <span>{hero.badge}</span>
            </span>
            <h1 id="hero-title" style={{ margin: 0, fontSize: "clamp(46px, 6vw, 80px)", lineHeight: 0.96, fontWeight: 900, fontStretch: "112%", letterSpacing: "-2.6px" }}>{hero.heading}</h1>
            <p style={{ margin: 0, fontSize: 20, lineHeight: 1.5, color: "#4B4F6B", maxWidth: "29em" }}>{hero.sub}</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "center" }}>
              <a href={hero.primary.href} className="lx-btn lx-btn-ink" style={{ fontSize: 18, fontWeight: 900, padding: "19px 30px", boxShadow: "0 14px 30px rgba(20,22,43,0.22)" }} data-testid="hero-cta">{hero.primary.label}</a>
              {hero.video.href ? (
                <a key="video" href={hero.video.href} style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 12, fontSize: 17, fontWeight: 700, padding: "12px 8px" }} data-testid="hero-video">
                  <span style={{ display: "inline-flex", width: 44, height: 44, borderRadius: 99, background: "#FFD24A", alignItems: "center", justifyContent: "center", boxShadow: "0 8px 20px rgba(255,190,40,0.35)" }}>
                    <svg width="13" height="14" viewBox="0 0 13 14" aria-hidden="true"><path d="M2 1.5 L11.5 7 L2 12.5 Z" fill="#14162B" /></svg>
                  </span>
                  <span>{hero.video.label}</span>
                </a>
              ) : null}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px 22px", fontSize: 15, fontWeight: 600, color: "#4B4F6B" }}>
              {hero.reassure.map((r) => (
                <span key={r} style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
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
