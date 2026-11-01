/**
 * The approved Commercial Access application says "Etsy" is not in our page titles, so it is not in
 * any title, share title, JSON-LD name or headline, or h1. It stays in descriptions and body copy,
 * describing who the product is for. The checks run while pages are built, so a page that breaks the
 * rule fails `next build`, as well as the tests. Headings typed into a page are checked before the
 * build by scripts/heading-rule.cjs.
 */

const NAMES_ETSY = /etsy/i;

export function refuseEtsy(text: string, where: string): string {
  if (NAMES_ETSY.test(text)) throw new Error(`"Etsy" may not appear in ${where}: ${text}`);
  return text;
}

/** Every name and headline in a block of structured data, however deep. */
export function jsonLdNames(node: unknown): string[] {
  if (Array.isArray(node)) return node.flatMap(jsonLdNames);
  if (!node || typeof node !== "object") return [];
  return Object.entries(node).flatMap(([key, value]) =>
    (key === "name" || key === "headline") && typeof value === "string" ? [value] : jsonLdNames(value));
}
