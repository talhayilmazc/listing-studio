// What a ticked shop still needs before its listings can become drafts there
// (the review page's approve step). The server says what; this says what the
// seller can do about it.

import type { ShopNeed } from "./types";

/**
 * The one thing to do about a need:
 *  - "setup": the profile is not set up in the shop; one click starts it.
 *  - "wait": it is being set up now; the page looks again by itself.
 *  - "profiles": set up, but something of that shop is missing (a shipping
 *    profile, a return policy, ...): chosen or created on the Profiles page.
 *  - "card": a section the shop does not have; picked on the listing's card.
 *  - null: nothing to press (the message says what to do).
 */
export function needAction(need: ShopNeed): "setup" | "wait" | "profiles" | "card" | null {
  if (need.checking || need.kind === "checking") return "wait";
  if (need.kind === "profile") return need.setup && need.profile_id ? "setup" : null;
  if (need.kind === "section") return "card";
  if (["shipping_profile", "return_policy", "readiness_state", "production_partners"].includes(need.kind)) return "profiles";
  return null;
}

/** One line over a shop's needs: whether drafts are held back, or only made without something. */
export function needsHeading(needs: ShopNeed[]): string {
  const held = needs.filter((n) => n.blocking).reduce((most, n) => Math.max(most, n.listings), 0);
  if (held > 0) return `No draft is created here for ${held} listing${held === 1 ? "" : "s"} until this is settled:`;
  return "Drafts are created here, without this:";
}

/** Whether anything is still being set up, so the page should look again. */
export const anyChecking = (columns: { needs: ShopNeed[] }[]) =>
  columns.some((c) => c.needs.some((n) => needAction(n) === "wait"));
