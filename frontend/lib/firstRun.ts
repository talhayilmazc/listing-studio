/**
 * The first-run guide on Overview (B9): four steps from a new account to drafts on Etsy, each
 * ticked from the account's own data, never from what the seller clicked. Hidden once every
 * step is done or the seller closes it (this browser remembers, a convenience).
 */

export interface Step {
  key: "shop" | "profile" | "upload" | "send";
  title: string;
  body: string;
  href: string;
  done: boolean;
}

export interface Facts {
  shops: number;
  confirmedProfiles: number;
  /** Confirmed profiles without How it's made answers: the profile step is not finished (lib/howMade.ts). */
  profilesWithoutHowMade?: number;
  batches: number;
  /** Listings with a draft or live in a shop (any batch). */
  sent: number;
}

export function steps(f: Facts): Step[] {
  return [
    { key: "shop", title: "Connect a shop", body: "Sign in to Etsy as the shop's owner.", href: "/connect", done: f.shops > 0 },
    {
      key: "profile",
      title: "Make a profile",
      body: (f.profilesWithoutHowMade ?? 0) > 0 && f.confirmedProfiles > 0
        ? `Set How it's made on ${f.profilesWithoutHowMade} profile${f.profilesWithoutHowMade === 1 ? "" : "s"}: it says what to tick in Etsy after publishing.`
        : "Pick one of your own listings to copy category, price and shipping from.",
      href: "/profiles",
      done: f.confirmedProfiles > 0 && (f.profilesWithoutHowMade ?? 0) === 0,
    },
    { key: "upload", title: "Upload designs", body: "Photos, folders or a ZIP; each listing's photos together.", href: "/upload", done: f.batches > 0 },
    { key: "send", title: "Review and send", body: "Approve each listing, then choose where and when it goes.", href: "/batches", done: f.sent > 0 },
  ];
}

export const DISMISS_KEY = "listyro.guide.closed";

export function closedHere(): boolean {
  try {
    return globalThis.localStorage?.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function closeHere(): void {
  try {
    globalThis.localStorage?.setItem(DISMISS_KEY, "1");
  } catch {
    /* not remembered: shown again next time */
  }
}
