/**
 * Where size charts sit among a listing's photos: the TypeScript copy of the
 * server's rules (backend app/pipeline/chart_order.py). One slot per chart: the
 * number of photos before it, or END for "after every photo". The cover is
 * always first, so a slot is at least 1.
 */
export const END = -1;
export type ChartPosition = "after_cover" | "third" | "last";

export const POSITION_LABELS: Record<ChartPosition, string> = {
  after_cover: "After the cover (2nd)",
  third: "3rd",
  last: "Last",
};

export function defaultSlots(position: ChartPosition | null | undefined, charts: number): number[] {
  const slot = position === "after_cover" ? 1 : position === "third" ? 2 : END;
  return Array.from({ length: charts }, () => slot);
}

/** Photos (cover first) and charts in listing order. */
export function arrange<T, U>(photos: T[], charts: U[], slots: number[]): (T | U)[] {
  const n = photos.length;
  if (!n) return [...charts];
  const place = charts.map((_, i) => {
    const s = slots[i] ?? END;
    return s === END ? n : Math.min(Math.max(1, s), n);
  });
  const out: (T | U)[] = [];
  photos.forEach((p, k) => {
    out.push(p);
    if (k + 1 < n) charts.forEach((c, i) => place[i] === k + 1 && out.push(c));
  });
  charts.forEach((c, i) => place[i] >= n && out.push(c));
  return out;
}

/** The slots a dragged strip means, one per chart in ``chartIds`` order. */
export function slotsFromOrder(order: string[], chartIds: string[]): number[] {
  const charts = new Set(chartIds);
  const total = order.filter((x) => !charts.has(x)).length;
  const before = new Map<string, number>();
  let seen = 0;
  for (const x of order) {
    if (charts.has(x)) before.set(x, seen >= total ? END : Math.max(1, seen));
    else seen += 1;
  }
  return chartIds.map((c) => before.get(c) ?? END);
}

export const sameSlots = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);
