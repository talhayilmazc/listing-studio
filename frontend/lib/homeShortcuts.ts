/**
 * Home's shortcuts (feature/mockups-visible): every feature a seller uses weekly is one click from the menu
 * or from Home. The menu holds the daily work (components/Rail.tsx); Home holds the rest, so the menu stays at
 * seven entries plus Settings.
 */
export type Shortcut = { href: string; title: string; line: string; testid: string };

/** The mockups card: make them when there is a template to make them with, else add the first one. */
export function mockupsCard(templates: number | null): Shortcut | null {
  if (templates === null) return null; // not read yet: nothing rather than the wrong card
  if (templates > 0) {
    return {
      href: "/mockups", title: "Make mockups", testid: "home-make-mockups",
      line: `Put your designs on your ${templates === 1 ? "template" : `${templates} templates`} and download them or use them for listings.`,
    };
  }
  return {
    href: "/mockups/templates", title: "Add your first mockup template", testid: "home-first-template",
    line: "A blank photo of your product, or a Photoshop mockup (PSD). Your designs are placed on it.",
  };
}

/** The other features a seller opens each week or season, one click away. */
export const SHORTCUTS: Shortcut[] = [
  { href: "/listing-health", title: "Listing health", testid: "home-listing-health",
    line: "Your live listings against Etsy's guidance, worst first, with fixes you approve." },
  { href: "/season-planner", title: "Season planner", testid: "home-season-planner",
    line: "Spread listings over the weeks before a holiday." },
  { href: "/profiles", title: "Profiles", testid: "home-profiles",
    line: "The reference listings each new listing copies its settings from." },
];
