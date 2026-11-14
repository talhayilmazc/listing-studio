/**
 * Pages that moved (feature/mockups-visible): Mockups left Designs and Settings for their own menu entry. The
 * old addresses (bookmarks, links in notes already shown) keep working: each is a page that sends the seller to
 * the new one, query and all (?run=, ?design=, ?templates=). Pages, not next.config redirects: an app address
 * stays private like every app page (lib/marketing/routes.mjs, docs/seo.md).
 */
export const MOVED: Record<string, string> = {
  "/templates": "/mockups/templates",
  "/templates/designs": "/mockups",
};

/** Where an old address goes now, with its query. */
export function movedTo(from: string, query: Record<string, string | string[] | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    for (const v of Array.isArray(value) ? value : value === undefined ? [] : [value]) params.append(key, v);
  }
  const rest = params.toString();
  return `${MOVED[from]}${rest ? `?${rest}` : ""}`;
}
