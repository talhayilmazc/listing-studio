import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/marketing/pages";

/** Every folder in app/(app), and the API (lib/marketingSite.test.ts compares this with the folders). */
export const APP_PATHS = [
  "/accept-terms", "/admin", "/analytics", "/batches", "/connect", "/dashboard", "/listing-health", "/season-planner", "/login", "/password",
  "/profiles", "/register", "/scheduled", "/settings", "/templates", "/upload",
];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/api/", ...APP_PATHS] },
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
