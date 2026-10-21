// A reference listing (profile) in the seller's shops. Since 21 October 2026 it uses its main
// shop's shipping profile, return policy and processing profile in every shop, with no
// question: each is matched (a choice the seller made, the same name or terms) or copied into
// the shop at once. There is no setup dialog and no "Finish setup": a shop is ready, or
// "preparing…" while a copy is made, or shows one plain line when Etsy refused something.
// The seller can change which of a shop's own settings is used ("change"): that choice is the
// account's, for every reference listing using the same setting.
//
// Pure (components/ProfileShops.tsx and ShopSettingsPanel.tsx draw it; lib/shopSetup.test.ts
// checks it).

import type { ProfileLink, SettingUse, ShopSetup } from "./types";

/** The one line at the bottom of a reference listing card. */
export function settingsLine(mainShop: string | null | undefined): string {
  return `Shipping, returns and processing: same as ${mainShop || "the main shop"} in every shop`;
}

export type ShopState =
  | { kind: "main"; text: string }
  | { kind: "ready"; text: string }
  | { kind: "preparing"; text: string }
  | { kind: "problem"; text: string };

/** How one shop shows on the card. A problem is one plain line: Etsy's reason and what to do. */
export function shopState(link: Pick<ProfileLink, "main" | "ready" | "status" | "reason">): ShopState {
  if (link.main) return { kind: "main", text: "main shop" };
  if (link.status === "checking") return { kind: "preparing", text: "preparing…" };
  if (link.ready) return { kind: "ready", text: "ready" };
  return { kind: "problem", text: link.reason || "could not be prepared; try again with Use in" };
}

/** The settings the change panel offers for one shop: shipping, returns and processing, each
 * with that shop's own choices and the one in use (production partners stay as they are). */
export const CHANGEABLE = ["shipping_profile", "return_policy", "readiness_state"] as const;

export interface PanelSetting {
  resource: string;
  label: string;
  value: number | null;
  options: { id: number; label: string }[];
}

export function panelSettings(shop: ShopSetup): PanelSetting[] {
  return shop.settings
    .filter((s: SettingUse) => (CHANGEABLE as readonly string[]).includes(s.resource))
    .map((s) => {
      const value = s.linked_ids.length === 1 ? s.linked_ids[0] : null;
      const options = [...s.options];
      if (value !== null && !options.some((o) => o.id === value)) {
        options.unshift({ id: value, label: s.linked_label ?? `#${value}` });
      }
      return { resource: s.resource, label: s.label, value, options };
    });
}

/**
 * The shops "Use in" offers for a profile: every shop of the account except the
 * profile's main shop, each with where it stands. A shop the profile is already used in is
 * offered too: choosing it again sets it up again.
 */
export function useInShops(
  shops: { id: string; name: string }[],
  links: { connection_id: string; main: boolean; ready: boolean; status: string }[],
): { id: string; name: string; note: string | null }[] {
  const main = links.find((l) => l.main)?.connection_id;
  return shops
    .filter((s) => s.id !== main)
    .map((s) => {
      const link = links.find((l) => l.connection_id === s.id);
      const note = !link ? null
        : link.status === "checking" ? "preparing…"
        : link.ready ? "ready"
        : "needs a look";
      return { id: s.id, name: s.name, note };
    });
}

/** Fired when a profile's setup in a shop changed: every profile card on the page reloads,
 * since one setting is shared by every reference listing that uses it (the account's map). */
export const PROFILES_CHANGED = "listyro:profiles-changed";

export function announceProfilesChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(PROFILES_CHANGED));
}
