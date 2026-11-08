import { SiteFooter, SiteHeader } from "@/components/marketing/Site";

// The visit counter: cookieless, and on these pages only. Set at build time; empty means none.
// It reads no cookie, keeps nothing in the browser and does not identify anyone (Privacy Policy).
const ANALYTICS = process.env.NEXT_PUBLIC_CF_ANALYTICS_TOKEN;

/** The public site. Static pages: no session lookup, none of the app's code. */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="site flex min-h-screen flex-col bg-white text-ink">
      {ANALYTICS ? <script key="visits" defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon={JSON.stringify({ token: ANALYTICS })} /> : null}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow"
      >
        Skip to content
      </a>
      <SiteHeader />
      <main id="main" className="flex-1">{children}</main>
      <SiteFooter />
    </div>
  );
}
