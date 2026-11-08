import { Logo } from "@/components/Logo";
import { TRADEMARK_NOTICE } from "@/lib/legal";
import { PRIMARY_CTA } from "@/lib/marketing/cta";

/**
 * The public site's frame, as the home page reference draws it (docs/design/home-reference.dc.html): static,
 * no session, no app code. Links are plain <a>: each click is a whole page the edge can answer.
 */

// The notice itself lives in lib/legal.ts (one copy for every page).
export { TRADEMARK_NOTICE };

/** The reference's four. On the home page the first two scroll to its sections. */
export const TOP_NAV = [
  { label: "How it works", href: "/#how" },
  { label: "Safety", href: "/#safe" },
  { label: "Pricing", href: "/pricing" },
  { label: "Free tools", href: "/free-tools" },
];

/** The footer's links, in the reference's order. */
export const FOOTER_NAV = [
  { label: "Features", href: "/features" },
  { label: "Pricing", href: "/pricing" },
  { label: "Free tools", href: "/free-tools" },
  { label: "Guides", href: "/guides" },
  { label: "Compare", href: "/compare" },
  { label: "Help", href: "/help" },
  { label: "Contact", href: "/contact" },
  { label: "Terms", href: "/terms" },
  { label: "Privacy", href: "/privacy" },
];

export function SiteHeader() {
  return (
    <header className="lx-ref" style={{ position: "relative", zIndex: 5 }}>
      <div className="lx-wrap lx-head" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", height: 80, gap: 20 }}>
        <a href="/" className="lx-hit lx-logo" style={{ textDecoration: "none" }} aria-label="Listyro home">
          <Logo size={36} />
        </a>
        <nav className="lx-navl" aria-label="Main" style={{ fontSize: 15, fontWeight: 600 }} data-testid="top-nav">
          {TOP_NAV.map((item) => (
            <a key={item.href} href={item.href} className="lx-hit" style={{ textDecoration: "none" }}>{item.label}</a>
          ))}
        </nav>
        <div className="lx-head-actions" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <a href="/login" className="lx-hit lx-signin" style={{ textDecoration: "none", fontSize: 15, fontWeight: 600, padding: "12px 12px" }}>Sign in</a>
          <a href={PRIMARY_CTA.href} className="lx-btn lx-btn-ink lx-head-cta" style={{ fontSize: 15, fontWeight: 800, padding: "12px 20px", minHeight: 44 }} data-testid="header-cta">{PRIMARY_CTA.label}</a>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="lx-ref" style={{ borderTop: "1px solid #EEF0F5", padding: "48px 0 60px" }}>
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 26 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "20px 56px", justifyContent: "space-between", alignItems: "center" }}>
          <a href="/" className="lx-hit" style={{ textDecoration: "none" }} aria-label="Listyro home">
            <Logo size={32} />
          </a>
          <nav aria-label="Footer" style={{ display: "flex", flexWrap: "wrap", gap: "0 26px", fontSize: 15 }}>
            {FOOTER_NAV.map((item) => (
              <a key={item.href} href={item.href} className="lx-hit" style={{ textDecoration: "none" }}>{item.label}</a>
            ))}
          </nav>
        </div>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: "#6A6F8A" }} data-testid="trademark-notice">{TRADEMARK_NOTICE}</p>
      </div>
    </footer>
  );
}
