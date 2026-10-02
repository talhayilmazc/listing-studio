import type { MetadataRoute } from "next";

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://listyro.com";

/** The public pages only; everything behind sign-in stays out of search. */
export default function sitemap(): MetadataRoute.Sitemap {
  return ["", "/pricing", "/request-invite", "/contact", "/terms", "/privacy"].map((path) => ({
    url: `${SITE}${path}`,
    changeFrequency: path === "" ? "weekly" : "monthly",
  }));
}
