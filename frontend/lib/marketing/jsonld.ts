/**
 * Structured data for the public site. Only what the pages themselves say: no rating, no review and
 * no user count, because we have none to give. Offers come from lib/plans.ts and say they are
 * planned, since nothing can be bought today.
 */
import { PLANNED_NOTICE, PLANS, listingsFact, shopsFact, type Plan } from "../plans.ts";
import { BRAND_FILES } from "./brand.ts";
import { GUIDES, type Guide } from "./guides.ts";
import { SITE_URL, cardSlug, pageAt, trail } from "./pages.ts";
import type { Tool } from "./tools.ts";

const CONTEXT = "https://schema.org";
const url = (path: string) => `${SITE_URL}${path === "/" ? "" : path}`;

export const organization = () => ({
  "@context": CONTEXT,
  "@type": "Organization",
  name: "Listyro",
  url: url("/"),
  logo: `${SITE_URL}${BRAND_FILES.icon512Maskable}`,
});

const offer = (plan: Plan) => ({
  "@type": "Offer",
  name: plan.name,
  price: String(plan.price),
  priceCurrency: "USD",
  availability: "https://schema.org/PreOrder",
  url: url("/pricing"),
  description: `${listingsFact(plan)}, ${shopsFact(plan)}. ${PLANNED_NOTICE}`,
});

export const softwareApplication = () => ({
  "@context": CONTEXT,
  "@type": "SoftwareApplication",
  name: "Listyro",
  url: url("/"),
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  description: pageAt("/").description,
  offers: PLANS.map(offer),
});

export const faqPage = (items: { q: string; a: string }[]) => ({
  "@context": CONTEXT,
  "@type": "FAQPage",
  mainEntity: items.map((item) => ({
    "@type": "Question",
    name: item.q,
    // The page's own links are written [label](href): the label is the answer's text.
    acceptedAnswer: { "@type": "Answer", text: item.a.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") },
  })),
});

export const breadcrumbs = (path: string) => ({
  "@context": CONTEXT,
  "@type": "BreadcrumbList",
  itemListElement: trail(path).map((page, i) => ({ "@type": "ListItem", position: i + 1, name: page.crumb, item: url(page.path) })),
});

export const article = (guide: Guide) => {
  const path = `/guides/${guide.slug}`;
  return {
    "@context": CONTEXT,
    "@type": "Article",
    headline: guide.h1,
    description: guide.description,
    url: url(path),
    mainEntityOfPage: url(path),
    image: `${SITE_URL}/og/${cardSlug(path)}.png`,
    datePublished: guide.published,
    dateModified: guide.checked,
    author: { "@type": "Organization", name: "Listyro", url: url("/") },
    publisher: { "@type": "Organization", name: "Listyro", url: url("/") },
  };
};

/** A tool that runs on the page. Nothing to buy and no rating: only what the page itself says. */
export const toolApplication = (tool: Tool) => ({
  "@context": CONTEXT,
  "@type": "WebApplication",
  name: tool.name,
  url: url(`/free-tools/${tool.slug}`),
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  description: tool.description,
  isAccessibleForFree: true,
  provider: { "@type": "Organization", name: "Listyro", url: url("/") },
});

export const guideCount = GUIDES.length;
