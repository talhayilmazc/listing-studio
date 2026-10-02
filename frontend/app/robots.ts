import type { MetadataRoute } from "next";

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://listyro.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: ["/", "/pricing", "/request-invite", "/contact", "/terms", "/privacy"],
      disallow: ["/api/", "/dashboard", "/batches", "/upload", "/profiles", "/scheduled", "/analytics", "/settings", "/admin", "/connect"],
    },
    sitemap: `${SITE}/sitemap.xml`,
  };
}
