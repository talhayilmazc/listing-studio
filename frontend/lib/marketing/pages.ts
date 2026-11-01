/**
 * Every public page's title, description and place in the site: one registry. Metadata, the Open
 * Graph cards, breadcrumbs and the sitemap all read it, and lib/marketingSite.test.ts checks it
 * against lib/marketing/routes.mjs and the files in app/(marketing).
 */
import type { Metadata } from "next";
import { COMPARES, ALTERNATIVES, COMPARE_PAGE } from "./compare.ts";
import { FEATURES, FEATURES_PAGE } from "./features.ts";
import { GUIDES, GUIDES_PAGE } from "./guides.ts";
import { FREE_TOOLS, HELP } from "./help.ts";
import { HOME } from "../homeCopy.ts";
import { PRICING_HEADING } from "../plans.ts";

export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://listyro.com";

export type Page = {
  path: string;
  title: string;
  description: string;
  /** Shown on the page's share card. */
  card: string;
  /** The page above this one, for breadcrumbs. */
  parent?: string;
  /** The name in a breadcrumb. */
  crumb: string;
  /** ISO day, for the sitemap. */
  updated: string;
};

/** The day the site's pages were last changed in a way that matters to a search engine. */
const SITE_UPDATED = "2026-11-01";

const simple = (path: string, title: string, description: string, card: string, crumb: string, parent?: string): Page =>
  ({ path, title, description, card, crumb, parent, updated: SITE_UPDATED });

export const PAGES: Page[] = [
  simple("/", HOME.title, HOME.description, `${HOME.hero.heading} ${HOME.hero.headingAccent}`, "Home"),
  simple("/features", FEATURES_PAGE.title, FEATURES_PAGE.description, FEATURES_PAGE.h1, "Features", "/"),
  ...FEATURES.map((f) => simple(`/features/${f.slug}`, f.title, f.description, f.h1, f.name, "/features")),
  simple("/pricing", "Pricing: planned plans after launch | Listyro", "Listyro is in invite-only early access and no payment is taken today. The plans planned for after launch are shown; prices may change before launch.", PRICING_HEADING, "Pricing", "/"),
  simple("/free-tools", FREE_TOOLS.title, FREE_TOOLS.description, FREE_TOOLS.h1, "Free tools", "/"),
  simple("/compare", COMPARE_PAGE.title, COMPARE_PAGE.description, COMPARE_PAGE.h1, "Compare", "/"),
  ...COMPARES.map((c) => simple(`/compare/${c.slug}`, c.title, c.description, c.h1, c.h1, "/compare")),
  simple(`/compare/${ALTERNATIVES.slug}`, ALTERNATIVES.title, ALTERNATIVES.description, ALTERNATIVES.h1, "Alternatives", "/compare"),
  simple("/guides", GUIDES_PAGE.title, GUIDES_PAGE.description, GUIDES_PAGE.h1, "Guides", "/"),
  ...GUIDES.map((g) => ({ path: `/guides/${g.slug}`, title: g.title, description: g.description, card: g.h1, crumb: g.h1, parent: "/guides", updated: g.checked })),
  simple("/help", HELP.title, HELP.description, HELP.h1, "Help center", "/"),
  simple("/request-invite", "Request an invite | Listyro", "Listyro is invite-only while we onboard Etsy sellers in small groups. Tell us about your shop and we'll email you when a spot opens.", "Request an invite", "Request an invite", "/"),
  simple("/contact", "Contact | Listyro", "How to reach the people who make Listyro: support, questions about your account, and invite requests.", "Contact", "Contact", "/"),
  simple("/terms", "Terms of Service | Listyro", "The terms for using Listyro, including what you are responsible for in your own shop.", "Terms of Service", "Terms of Service", "/"),
  simple("/privacy", "Privacy Policy | Listyro", "What Listyro collects, why, how long it is kept, and how to delete it.", "Privacy Policy", "Privacy Policy", "/"),
];

const byPath = new Map(PAGES.map((page) => [page.path, page]));

export function pageAt(path: string): Page {
  const page = byPath.get(path);
  if (!page) throw new Error(`no marketing page at ${path}`);
  return page;
}

/** "/features/profiles" -> "features-profiles"; "/" -> "home". */
export const cardSlug = (path: string) => (path === "/" ? "home" : path.slice(1).replace(/\//g, "-"));
export const pageForCard = (slug: string) => PAGES.find((page) => cardSlug(page.path) === slug);

/** Home first, this page last. */
export function trail(path: string): Page[] {
  const page = pageAt(path);
  return page.parent ? [...trail(page.parent), page] : [page];
}

/** A page's own title, description, canonical address and share card. */
export function metadataFor(path: string, extra: Partial<Metadata> = {}): Metadata {
  const page = pageAt(path);
  const image = { url: `/og/${cardSlug(path)}.png`, width: 1200, height: 630, alt: page.card };
  return {
    title: { absolute: page.title },
    description: page.description,
    alternates: { canonical: path },
    openGraph: { type: "website", url: path, siteName: "Listyro", title: page.title, description: page.description, images: [image] },
    twitter: { card: "summary_large_image", title: page.title, description: page.description, images: [image.url] },
    ...extra,
  };
}
