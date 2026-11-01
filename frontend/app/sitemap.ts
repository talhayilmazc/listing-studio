import type { MetadataRoute } from "next";
import { SITE_URL, sitemapPages } from "@/lib/marketing/pages";

/** The public pages and guides only; everything behind sign-in stays out of search. */
export default function sitemap(): MetadataRoute.Sitemap {
  return sitemapPages().map((page) => ({
    url: `${SITE_URL}${page.path === "/" ? "" : page.path}`,
    lastModified: page.updated,
  }));
}
