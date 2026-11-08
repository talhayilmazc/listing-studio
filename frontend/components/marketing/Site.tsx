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
      <div className="lx-wrap lx-head">
        <a href="/" className="lx-hit lx-logo" aria-label="Listyro home">
          <Logo size={36} />
        </a>
        <nav className="lx-navl" aria-label="Main" data-testid="top-nav">
          {TOP_NAV.map((item) => (
            <a key={item.href} href={item.href} className="lx-hit">{item.label}</a>
          ))}
        </nav>
        <div className="lx-head-actions">
          <a href="/login" className="lx-hit lx-signin">Sign in</a>
          <a href={PRIMARY_CTA.href} className="lx-btn lx-btn-ink lx-head-cta" data-testid="header-cta">{PRIMARY_CTA.label}</a>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="lx-ref lx-foot">
      <div className="lx-wrap lx-foot-in">
        <div className="lx-foot-row">
          <a href="/" className="lx-hit" aria-label="Listyro home">
            <Logo size={32} />
          </a>
          <nav aria-label="Footer" className="lx-foot-nav">
            {FOOTER_NAV.map((item) => (
              <a key={item.href} href={item.href} className="lx-hit">{item.label}</a>
            ))}
          </nav>
        </div>
        <p className="lx-notice" data-testid="trademark-notice">{TRADEMARK_NOTICE}</p>
      </div>
    </footer>
  );
}
