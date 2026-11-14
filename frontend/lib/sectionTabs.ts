/**
 * The tabs under a page's title (components/SectionTabs.tsx): the pages that belong to one rail
 * entry (app/onboarding-v1). Designs holds the batches and the upload; Mockups the mockup maker, the
 * templates and the downloads (feature/mockups-visible); Schedule the season planner; Reports listing
 * health; Settings the account and profiles, and a link to the mockup templates (their page shows the
 * Mockups tabs: a page belongs to the first section that lists it).
 */
export type Tab = { href: string; label: string };

export const SECTION_TABS: Record<string, Tab[]> = {
  designs: [
    { href: "/batches", label: "Batches" },
    { href: "/upload", label: "Upload designs" },
  ],
  mockups: [
    { href: "/mockups", label: "Make mockups" },
    { href: "/mockups/templates", label: "Templates" },
    { href: "/mockups/downloads", label: "Downloads" },
  ],
  schedule: [
    { href: "/scheduled", label: "Scheduled" },
    { href: "/season-planner", label: "Season planner" },
  ],
  reports: [
    { href: "/analytics", label: "Profit and sales" },
    { href: "/listing-health", label: "Listing health" },
  ],
  settings: [
    { href: "/settings", label: "Account" },
    { href: "/profiles", label: "Profiles" },
    { href: "/mockups/templates", label: "Mockup templates" },
  ],
};

/** The tabs a page shows, and which is current; none on a page that is not one of them. */
export function tabsFor(pathname: string): { tabs: Tab[]; current: string } | null {
  for (const tabs of Object.values(SECTION_TABS)) {
    const current = tabs.find((t) => pathname === t.href);
    if (current) return { tabs, current: current.href };
  }
  return null;
}
