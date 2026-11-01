/**
 * The shape of the public site's long pages. Text may carry links as [label](href): components/
 * marketing/Page.tsx renders them, and lib/marketingSite.test.ts checks every one of them.
 */

export type Block =
  | { h2: string; id?: string }
  | { p: string }
  | { ul: string[] }
  | { ol: string[] }
  | { note: string }
  | { table: { caption: string; head: string[]; rows: string[][] } };

export type Source = { label: string; url: string };

/** Every string in a value, for the checks. */
export function allText(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (Array.isArray(node)) return node.flatMap((n) => allText(n));
  if (node && typeof node === "object") return Object.values(node).flatMap((n) => allText(n));
  return [];
}

/** The [label](href) links in a text. */
export function linksIn(text: string): { label: string; href: string }[] {
  return [...text.matchAll(/\[([^\]]+)\]\(([^)\s]+)\)/g)].map((m) => ({ label: m[1], href: m[2] }));
}

/** A long date, the same on the page whatever the reader's time zone. */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const month = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][m - 1];
  return `${d} ${month} ${y}`;
}
