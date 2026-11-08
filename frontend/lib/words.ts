/**
 * One name per thing, and an action keeps its name through the flow (app/onboarding-v1;
 * docs/app-audit.md): the button "Approve" gives the note "Approved", "Write listings" says
 * "Writing 3 of 12" and then "Listings ready". The rail and the page titles use NAV and TITLES
 * (components/Rail.tsx, components/TopStrip.tsx); the words of the work itself are here.
 */

export const WORDS = {
  upload: "Upload designs",
  write: "Write listings",
  approve: "Approve",
  approved: "Approved",
  approveAll: "Approve all",
  send: "Send",
  connect: "Connect your shop",
  connectAnother: "Connect another shop",
  examples: "Try the example designs",
} as const;

/** The pages, by the word the rail uses. */
export const PLACES = {
  home: "Home",
  designs: "Designs",
  review: "Review",
  schedule: "Schedule",
  reports: "Reports",
  shops: "Shops",
  settings: "Settings",
} as const;

/** What a run of writing did, in one line: "3 listings written", "2 written, 1 could not be written". */
export function writtenLine(written: number, failed: number, skipped = 0): string {
  const parts: string[] = [];
  parts.push(`${written} listing${written === 1 ? "" : "s"} written`);
  if (failed) parts.push(`${failed} could not be written`);
  if (skipped) parts.push(`${skipped} already written`);
  return parts.join(", ") + ".";
}
