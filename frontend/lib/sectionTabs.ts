/**
 * The tabs under a page's title (components/SectionTabs.tsx): the pages that belong to one rail
 * entry (app/onboarding-v1). Settings holds the account, profiles and mockup templates; Designs
 * the batches, the upload and the mockup maker; Schedule the season planner; Reports listing health.
 */
export type Tab = { href: string; label: string };

export const SECTION_TABS: Record<string, Tab[]> = {
  designs: [
    { href: "/batches", label: "Batches" },
    { href: "/upload", label: "Upload designs" },
    { href: "/templates/designs", label: "Make mockups" },
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
    { href: "/templates", label: "Mockup templates" },
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
