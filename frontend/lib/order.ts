// Image order within a listing group (docs/duzeltmeler-v6.md §E). The first id
// is the cover; Etsy receives the images in this order.

/** Move `id` to just before `target` (or to the end when `target` is null). */
export function moveBefore(ids: string[], id: string, target: string | null): string[] {
  if (id === target || !ids.includes(id)) return ids;
  const rest = ids.filter((x) => x !== id);
  const at = target === null ? rest.length : rest.indexOf(target);
  if (at < 0) return ids;
  return [...rest.slice(0, at), id, ...rest.slice(at)];
}

/** Move `id` one place left (-1) or right (+1). */
export function nudge(ids: string[], id: string, by: -1 | 1): string[] {
  const i = ids.indexOf(id);
  const j = i + by;
  if (i < 0 || j < 0 || j >= ids.length) return ids;
  const next = [...ids];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/** Make `id` the cover: first, everything else keeps its order. */
export function makeCover(ids: string[], id: string): string[] {
  return ids.includes(id) ? [id, ...ids.filter((x) => x !== id)] : ids;
}

export function sameOrder(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}
