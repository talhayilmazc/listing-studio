// The shop switcher's "All shops" and the shop groups it lists (multi-shop sellers).
// Pure: no React, no API, so the rules are tested on their own (lib/shopScope.test.ts).

/** Pages that can show every shop at once: a total and a row per shop (or that list
 *  every shop's work anyway). The switcher offers "All shops" only here; any other
 *  page works on one shop and shows the one picked last. */
export const ALL_SHOP_PAGES: RegExp[] = [/^\/dashboard$/, /^\/analytics$/, /^\/batches$/, /^\/scheduled$/, /^\/profiles$/];

export const supportsAll = (pathname: string | null | undefined): boolean =>
  ALL_SHOP_PAGES.some((re) => re.test(pathname ?? ""));

/** What the browser remembers for "All shops" (in place of a shop id). */
export const ALL = "all";

interface ShopLike {
  id: string;
  group_ids?: string[];
}

interface GroupLike {
  id: string;
  name: string;
}

/**
 * The shops under their group names, in group order, then the shops in no group.
 * A shop in two groups is listed under each (a seller's Group A and Group B can
 * share a shop).
 */
export function shopSections<S extends ShopLike>(shops: S[], groups: GroupLike[] | null): [string, S[]][] {
  if (!groups || groups.length === 0) return [["", shops]];
  const out: [string, S[]][] = [];
  for (const g of groups) {
    const members = shops.filter((s) => (s.group_ids ?? []).includes(g.id));
    if (members.length) out.push([g.name, members]);
  }
  const known = new Set(groups.map((g) => g.id));
  const rest = shops.filter((s) => !(s.group_ids ?? []).some((id) => known.has(id)));
  if (rest.length) out.push([out.length ? "No group" : "", rest]);
  return out;
}

/**
 * Which view the switcher is in. ``remembered`` is what the browser kept: a shop id,
 * "all", or nothing. With several shops and nothing remembered the view is every shop.
 * One shop: always that shop.
 */
export function resolveScope<S extends { id: string }>(
  shops: S[],
  remembered: string | null,
  lastSingle: string | null,
): { allShops: boolean; selected: S | null } {
  if (shops.length === 0) return { allShops: false, selected: null };
  const byId = (id: string | null) => (id ? shops.find((s) => s.id === id) ?? null : null);
  const single = byId(remembered) ?? byId(lastSingle) ?? shops[0];
  const allShops = shops.length > 1 && (remembered === ALL || byId(remembered) === null);
  return { allShops, selected: single };
}
