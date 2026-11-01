import { TRADEMARK_NOTICE } from "@/lib/legal";
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
    <a href="/" className={"tap inline-flex items-center gap-2.5 " + className} aria-label="Listyro home">
      <span aria-hidden className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 font-display text-xl leading-none text-white">
        L
      </span>
      <span className="font-display text-[1.7rem] leading-none text-slate-900">Listyro</span>
    </a>
  );
}

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-stone-50/95">
      <div className="mx-auto flex h-16 w-full max-w-[1240px] items-center justify-between gap-3 px-4 sm:px-6">
        <Wordmark />
        <nav aria-label="Site" className="hidden items-center gap-7 text-sm text-slate-700 lg:flex" data-testid="top-nav">
          {TOP_NAV.map((item) => <a key={item.href} href={item.href} className="hover:text-slate-900">{item.label}</a>)}
        </nav>
        <div className="flex items-center gap-1.5 sm:gap-2">
          <a href="/login" className="btn-ghost hidden sm:inline-flex">Sign in</a>
          <a href="/request-invite" className="btn-primary max-[359px]:px-2.5">Request an invite</a>
          {/* A menu with no script: <details> opens and closes by itself. */}
          <details className="relative lg:hidden" data-testid="site-menu">
            <summary className="btn-ghost cursor-pointer list-none [&::-webkit-details-marker]:hidden">Menu</summary>
            <nav aria-label="Menu" className="absolute right-0 top-full mt-2 w-56 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
              {TOP_NAV.map((item) => <a key={item.href} href={item.href} className="flex min-h-[2.75rem] items-center rounded-lg px-3 text-[0.9375rem] text-slate-800 hover:bg-stone-100">{item.label}</a>)}
              <a href="/help" className="flex min-h-[2.75rem] items-center rounded-lg px-3 text-[0.9375rem] text-slate-800 hover:bg-stone-100">Help center</a>
              <a href="/login" className="flex min-h-[2.75rem] items-center rounded-lg px-3 text-[0.9375rem] text-slate-800 hover:bg-stone-100">Sign in</a>
            </nav>
          </details>
        </div>
      </div>
    </header>
  );
}

const COLUMN_LINK = "tap hover:text-slate-900";

function FooterColumn({ title, links }: { title: string; links: { label: string; href: string }[] }) {
  return (
    <nav aria-label={title} className="min-w-0">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      <ul className="mt-3 space-y-2">
        {links.map((link) => <li key={link.href}><a href={link.href} className={COLUMN_LINK}>{link.label}</a></li>)}
      </ul>
    </nav>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-slate-200 bg-white">
      <div className="mx-auto w-full max-w-[1240px] space-y-8 px-4 py-10 text-sm text-slate-600 sm:px-6">
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
        <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-3 border-t border-slate-200 pt-6">
          <Wordmark />
          <p className="max-w-3xl leading-relaxed" data-testid="trademark-notice">{TRADEMARK_NOTICE}</p>
        </div>
      </div>
    </footer>
  );
}
