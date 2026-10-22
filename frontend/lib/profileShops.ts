/**
 * Every shop's profiles, grouped by their main shop (B6): the sidebar's "Profiles" section and
 * the Profiles page's shop filter and per-shop breakdown. Pure, so the rules are tested alone.
 */

export interface ProfileLike {
  id: string;
  name: string;
  content_template: string;
  connection_id: string;
  shop_name: string | null;
  confirmed: boolean;
  reference_title?: string | null;
  links?: { connection_id: string; status?: string }[];
}

/** The sidebar offers a search once there are more profiles than this. */
export const SEARCH_ABOVE = 10;

function fold(s: string): string {
  return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Name, template or reference title contains every word typed. */
export function matches(p: ProfileLike, query: string): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = fold(`${p.name} ${p.content_template.replace(/_/g, " ")} ${p.reference_title ?? ""}`);
  return words.every((w) => hay.includes(w));
}

/** [main shop's name, its profiles] in the account's shop order, then any shop not listed. */
export function byMainShop<P extends ProfileLike>(profiles: P[], shops: { id: string; name: string }[], query = ""): [string, P[]][] {
  const shown = profiles.filter((p) => matches(p, query));
  const order = new Map(shops.map((s, i) => [s.id, i]));
  const groups = new Map<string, { name: string; at: number; items: P[] }>();
  for (const p of shown) {
    const name = shops.find((s) => s.id === p.connection_id)?.name ?? p.shop_name ?? "Another shop";
    const g = groups.get(p.connection_id) ?? { name, at: order.get(p.connection_id) ?? shops.length, items: [] };
    g.items.push(p);
    groups.set(p.connection_id, g);
  }
  return [...groups.values()]
    .sort((a, b) => a.at - b.at || a.name.localeCompare(b.name))
    .map((g) => [g.name, g.items.sort((a, b) => a.name.localeCompare(b.name))]);
}

/** A profile belongs to a shop when it is its main shop or it is set up there. */
export function inShop(p: ProfileLike, shopId: string | null): boolean {
  if (!shopId) return true;
  return p.connection_id === shopId || (p.links ?? []).some((l) => l.connection_id === shopId);
}

export interface ShopRow {
  id: string;
  name: string;
  /** Confirmed profiles whose main shop this is. */
  main: number;
  /** Confirmed profiles of other shops set up here. */
  setUp: number;
}

/** The per-shop breakdown under the Profiles page's figures. */
export function perShop(profiles: ProfileLike[], shops: { id: string; name: string }[]): ShopRow[] {
  return shops.map((s) => {
    const confirmed = profiles.filter((p) => p.confirmed);
    return {
      id: s.id,
      name: s.name,
      main: confirmed.filter((p) => p.connection_id === s.id).length,
      setUp: confirmed.filter((p) => p.connection_id !== s.id && (p.links ?? []).some((l) => l.connection_id === s.id)).length,
    };
  });
}
