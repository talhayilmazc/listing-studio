// Choosing where listings (or a profile) go: a shop group, or single shops.
// Pure (components/ShopTargetPicker.tsx draws it; lib/shopTargets.test.ts checks it).
// Nothing is chosen by default: the seller picks a group or the shops.

/** Whether every shop of a group that can be chosen is chosen. */
export function groupState(selected: string[], groupShops: string[], available: string[]): "all" | "some" | "none" {
  const usable = groupShops.filter((id) => available.includes(id));
  if (usable.length === 0) return "none";
  const on = usable.filter((id) => selected.includes(id)).length;
  return on === 0 ? "none" : on === usable.length ? "all" : "some";
}

/** Choose (or un-choose) every shop of a group that can be chosen; other shops stay as they are.
 *  A shop also in another chosen group stays chosen when this group is un-chosen only if the
 *  seller ticked it on its own; a group is a shortcut for its shops, not a separate target. */
export function toggleGroup(selected: string[], groupShops: string[], available: string[], on: boolean): string[] {
  const usable = groupShops.filter((id) => available.includes(id));
  if (on) return [...selected, ...usable.filter((id) => !selected.includes(id))];
  return selected.filter((id) => !usable.includes(id));
}

export function toggleShop(selected: string[], id: string, on: boolean): string[] {
  return on ? (selected.includes(id) ? selected : [...selected, id]) : selected.filter((x) => x !== id);
}

/** Keep the shops in the account's order (the order the switcher lists them). */
export const inOrder = (selected: string[], order: string[]) => order.filter((id) => selected.includes(id));
