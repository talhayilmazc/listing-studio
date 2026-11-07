import { Logo } from "@/components/Logo";
import { TRADEMARK_NOTICE } from "@/lib/legal";
import { PRIMARY_CTA } from "@/lib/marketing/cta";
import { COMPARES } from "@/lib/marketing/compare";
import { FEATURES } from "@/lib/marketing/features";
import { GUIDES } from "@/lib/marketing/guides";

/**
 * The public site's frame: static, no session, no app code. Links are plain <a>: each click is a
 * whole page the edge can answer, and no page asks the server for the pages it merely links to.
 */

// The notice itself lives in lib/legal.ts (one copy for every page).
export { TRADEMARK_NOTICE };

/** At most five, by decision. The rest is one click away in the footer. */
export const TOP_NAV = [
  { label: "Features", href: "/features" },
  { label: "Pricing", href: "/pricing" },
  { label: "Free tools", href: "/free-tools" },
  { label: "Compare", href: "/compare" },
  { label: "Guides", href: "/guides" },
];

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <a href="/" className={"tap inline-flex items-center " + className} aria-label="Listyro home">
      <Logo size={26} />
    </a>
  );
}

const MENU_LINK = "flex min-h-[2.75rem] items-center px-3 text-[0.9375rem] font-medium text-ink hover:bg-fabric";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-heather bg-white">
      <div className="mk-wrap flex h-16 items-center justify-between gap-3">
        <Wordmark />
        <nav aria-label="Site" className="hidden items-center gap-7 text-[0.9375rem] font-medium text-ink lg:flex" data-testid="top-nav">
          {TOP_NAV.map((item) => <a key={item.href} href={item.href} className="py-2 hover:underline hover:decoration-tag hover:decoration-[3px] hover:underline-offset-[6px]">{item.label}</a>)}
        </nav>
        <div className="flex items-center gap-1 sm:gap-3">
          <a href="/login" className="hidden px-2 py-2 text-[0.9375rem] font-medium text-ink hover:underline sm:inline-flex">Sign in</a>
          <a href={PRIMARY_CTA.href} className="mk-btn min-h-[2.75rem] px-3 !text-[0.875rem] sm:px-4 sm:!text-[0.9375rem] max-[359px]:px-2" data-testid="header-cta">{PRIMARY_CTA.label}</a>
          {/* A menu with no script: <details> opens and closes by itself. */}
          <details className="relative lg:hidden" data-testid="site-menu">
            <summary className="flex min-h-[2.75rem] cursor-pointer list-none items-center pl-2 text-[0.9375rem] font-medium text-ink [&::-webkit-details-marker]:hidden">Menu</summary>
            <nav aria-label="Menu" className="absolute right-0 top-full mt-2 w-56 border border-heather bg-white py-1">
              {TOP_NAV.map((item) => <a key={item.href} href={item.href} className={MENU_LINK}>{item.label}</a>)}
              <a href="/help" className={MENU_LINK}>Help center</a>
              <a href="/login" className={MENU_LINK}>Sign in</a>
            </nav>
          </details>
        </div>
      </div>
    </header>
  );
}

function FooterColumn({ title, links }: { title: string; links: { label: string; href: string }[] }) {
  return (
    <nav aria-label={title} className="min-w-0">
      <h2 className="text-[0.9375rem] font-bold text-ink">{title}</h2>
      <ul className="mt-3 space-y-2">
        {links.map((link) => <li key={link.href}><a href={link.href} className="tap text-slate-600 hover:text-ink hover:underline">{link.label}</a></li>)}
      </ul>
    </nav>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-heather bg-fabric">
      <div className="mk-wrap space-y-10 py-12 text-[0.9375rem]">
        <div className="grid grid-cols-2 gap-x-6 gap-y-8 md:grid-cols-4">
          <FooterColumn title="Features" links={[{ label: "All features", href: "/features" }, ...FEATURES.map((f) => ({ label: f.name, href: `/features/${f.slug}` }))]} />
          <FooterColumn title="Guides" links={[{ label: "All guides", href: "/guides" }, ...GUIDES.slice(0, 4).map((g) => ({ label: g.h1, href: `/guides/${g.slug}` }))]} />
          <FooterColumn title="Compare" links={[...COMPARES.map((c) => ({ label: c.h1, href: `/compare/${c.slug}` })), { label: "Alternatives", href: "/compare/alternatives" }, { label: "Free tools", href: "/free-tools" }]} />
          <FooterColumn title="Listyro" links={[
            { label: "Pricing", href: "/pricing" },
            { label: "Help center", href: "/help" },
            { label: "Request an invite", href: "/request-invite" },
            { label: "Contact", href: "/contact" },
            { label: "Terms of Service", href: "/terms" },
            { label: "Privacy Policy", href: "/privacy" },
          ]} />
        </div>
        <div className="flex flex-col gap-4 border-t border-heather pt-6 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
          <Wordmark />
          <p className="max-w-3xl leading-relaxed text-slate-600" data-testid="trademark-notice">{TRADEMARK_NOTICE}</p>
        </div>
      </div>
    </footer>
  );
}
