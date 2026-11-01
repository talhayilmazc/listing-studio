/**
 * The public site's addresses and the cache policy that follows from them: the one list.
 *
 * next.config.mjs builds its header rules from this, the sitemap and the content modules are
 * checked against it (lib/marketingSite.test.ts), and e2e/headers.spec.ts reads the real headers
 * of every route. A plain .mjs file because next.config.mjs has to import it.
 *
 * The rule, in the order safety needs it:
 *   - everything is `private, no-store` unless it is listed here. A route nobody thought about is
 *     private, and an app page can only become public by being added to this file on purpose.
 *   - a listed page is public only when the request has no session cookie and no router marker.
 *     Next answers a request with the `RSC` header on the page's own address with its router
 *     payload, not its HTML: cached, it would replace the page for everybody. Middleware cannot
 *     see that header (Next hides it), so this has to be a next.config rule.
 */

export const FEATURE_SLUGS = ["ai-listing-writer", "mockup-generator", "multi-shop-publishing", "scheduling", "profiles", "profit-report"];

export const GUIDE_SLUGS = [
  "etsy-title-rules-2026",
  "list-print-on-demand-shirts-in-bulk",
  "etsy-tags-13-tags-20-characters",
  "holiday-listing-calendar-pod",
  "running-more-than-one-etsy-shop",
  "mockups-buyers-trust",
  "trademarks-and-ip-on-etsy",
];

export const COMPARE_SLUGS = ["listyro-vs-listadum", "listyro-vs-mydesigns", "listyro-vs-vela", "alternatives"];

/** Every public page. Same answer for everybody, and nothing about who is asking. */
export const MARKETING_PAGES = [
  "/",
  "/features",
  ...FEATURE_SLUGS.map((slug) => `/features/${slug}`),
  "/pricing",
  "/free-tools",
  "/compare",
  ...COMPARE_SLUGS.map((slug) => `/compare/${slug}`),
  "/guides",
  ...GUIDE_SLUGS.map((slug) => `/guides/${slug}`),
  "/help",
  "/request-invite",
  "/contact",
  "/terms",
  "/privacy",
];

/** Files, and the two generated text files. Nothing behind these depends on a request. */
export const PUBLIC_FILES = ["/screens/:path*", "/og/:path*", "/icon.svg", "/favicon.ico", "/robots.txt", "/sitemap.xml"];

export const SESSION_COOKIE = "session";

export const CACHE_PRIVATE = "private, no-store";
/** An hour at the edge; a revalidation after that costs one 304 from us. */
export const CACHE_PAGE = "public, s-maxage=3600, stale-while-revalidate=86400";
/** A picture that changes gets a new file name (docs/marketing-screens.md). */
export const CACHE_FILE = "public, max-age=86400, s-maxage=604800, stale-while-revalidate=86400";

/** Any of these on a request and a marketing page stays private. */
export const PRIVATE_WHEN_PRESENT = [
  { type: "cookie", key: SESSION_COOKIE },
  { type: "header", key: "rsc" },
  { type: "header", key: "next-router-prefetch" },
  { type: "header", key: "next-router-state-tree" },
  { type: "header", key: "next-router-segment-prefetch" },
  { type: "query", key: "_rsc" },
];

const control = (value) => [{ key: "Cache-Control", value }];

/** next.config.mjs `headers()`: a later rule replaces an earlier one's header of the same name. */
export function cacheRules() {
  return [
    // Next's own static files are left alone (immutable, hashed names); /api answers for itself.
    { source: "/((?!_next/static|api/).*)", headers: control(CACHE_PRIVATE) },
    ...MARKETING_PAGES.map((source) => ({ source, missing: PRIVATE_WHEN_PRESENT, headers: control(CACHE_PAGE) })),
    ...PUBLIC_FILES.map((source) => ({ source, headers: control(CACHE_FILE) })),
  ];
}
