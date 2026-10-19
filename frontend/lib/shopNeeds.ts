// What a ticked shop still needs before its listings can become drafts there
// (the review page's approve step). The server says what; this says what the
// seller can do about it.

import type { ShopNeed } from "./types";

const SETTINGS = ["shipping_profile", "return_policy", "readiness_state", "production_partners"];

/**
 * The one thing to do about a need:
 *  - "setup": the profile is not set up in the shop, or that shop lacks one of its own
 *    settings (a processing profile, a shipping profile, a return policy, ...): the
 *    setup dialog opens right here (components/ShopSetupDialog.tsx): what matches is
 *    used, a copy is offered where the shop has none, one confirm for every shop.
 *  - "wait": it is being set up now; the page looks again by itself.
 *  - "card": a section the shop does not have; picked on the listing's card.
 *  - null: nothing to press (the message says what to do).
 */
export function needAction(need: ShopNeed): "setup" | "wait" | "card" | null {
  if (need.checking || need.kind === "checking") return "wait";
  if (need.kind === "section") return "card";
  if ((need.kind === "profile" || SETTINGS.includes(need.kind)) && need.profile_id) return "setup";
  return null;
}

/** Every profile x shop the dialog can set up, from the shops' needs: one dialog for all of them. */
export function setupItems(
  columns: { connection_id: string; needs: ShopNeed[] }[],
  only?: string[],
): { profileId: string; profileName: string | null; shopIds: string[] }[] {
  const out: { profileId: string; profileName: string | null; shopIds: string[] }[] = [];
  for (const col of columns) {
    if (only && !only.includes(col.connection_id)) continue;
    for (const n of col.needs) {
      if (needAction(n) !== "setup" || !n.profile_id) continue;
      let item = out.find((i) => i.profileId === n.profile_id);
      if (!item) out.push((item = { profileId: n.profile_id, profileName: n.profile_name ?? null, shopIds: [] }));
      if (!item.shopIds.includes(col.connection_id)) item.shopIds.push(col.connection_id);
    }
  }
  return out;
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
