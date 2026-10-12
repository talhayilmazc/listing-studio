/**
 * Where size charts sit among a listing's photos: the TypeScript copy of the
 * server's rules (backend app/pipeline/chart_order.py). One slot per chart: the
 * number of photos before it, or END for "after every photo". The cover is
 * always first, so a slot is at least 1.
 *
 * A placement is the charts in the order they appear, each with its slot. The
 * profile's is the default for its listings; a listing group's own wins. A
 * listing with fewer photos than a chart's slot gets that chart last.
 */
export const END = -1;
/** The most images Etsy keeps on a listing: the profile editor shows this many places. */
export const MAX_IMAGES = 20;
export type ChartPosition = "after_cover" | "third" | "last";

/** One chart's place: the photos before it, or END. */
export interface Placement {
  listing_image_id: number;
  slot: number;
}

export const chartKey = (id: number) => `chart:${id}`;
export const isChart = (tile: string) => tile.startsWith("chart:");
const chartId = (tile: string) => Number(tile.slice("chart:".length));

/** What one line says about the rule for short listings. */
export const FEWER_PHOTOS_RULE = "If a listing has fewer photos than the place you chose, the chart goes last.";
export const NOT_ON_ETSY_NOTE =
  "Changing this never changes drafts already on Etsy. It applies to new drafts and to Replace images.";

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

export const samePlacement = (a: Placement[], b: Placement[]) =>
  a.length === b.length && a.every((p, i) => p.listing_image_id === b[i].listing_image_id && p.slot === b[i].slot);

/** A preset as a placement: the charts together, in their own order. */
export function presetPlacement(position: ChartPosition | null | undefined, chartIds: number[]): Placement[] {
  const slots = defaultSlots(position, chartIds.length);
  return chartIds.map((id, i) => ({ listing_image_id: id, slot: slots[i] }));
}

/**
 * The profile editor's row of places, in listing order: "cover", then "photo:n"
 * placeholders (stand-ins for the listing's mockups), with a "chart:<id>" tile
 * for each chart where it would sit on a listing that has every place filled.
 */
export function editorStrip(placement: Placement[], total: number = MAX_IMAGES): string[] {
  const charts = placement.map((p) => chartKey(p.listing_image_id));
  const photos = Array.from({ length: Math.max(2, total - charts.length) }, (_, i) => (i === 0 ? "cover" : `photo:${i + 1}`));
  return arrange(photos, charts, placement.map((p) => p.slot));
}

/** The placement a row of tiles means: the charts in the order they appear, each with its slot. */
export function placementFromStrip(strip: string[]): Placement[] {
  const charts = strip.filter(isChart);
  const slots = slotsFromOrder(strip, charts);
  return charts.map((tile, i) => ({ listing_image_id: chartId(tile), slot: slots[i] }));
}

/** The same charts under a group's own tiles (ids in strip order, with their slots). */
export function placementFromOrder(order: string[], chartKeys: string[]): Placement[] {
  const inOrder = order.filter((x) => chartKeys.includes(x));
  const slots = slotsFromOrder(order, inOrder);
  return inOrder.map((tile, i) => ({ listing_image_id: chartId(tile), slot: slots[i] }));
}

/**
 * Put a chart at a place in the row (0-based index). It can go anywhere but
 * first: the cover is always a design mockup.
 */
export function moveChartTo(strip: string[], chart: string, index: number): string[] {
  if (!isChart(chart) || !strip.includes(chart)) return strip;
  const rest = strip.filter((x) => x !== chart);
  const at = Math.min(Math.max(1, index), rest.length);
  return [...rest.slice(0, at), chart, ...rest.slice(at)];
}

/** One place earlier (-1) or later (+1); never into the cover's place. */
export function nudgeChart(strip: string[], chart: string, by: -1 | 1): string[] {
  const i = strip.indexOf(chart);
  if (i < 0 || !isChart(chart)) return strip;
  const j = i + by;
  if (j < 1 || j >= strip.length) return strip;
  return moveChartTo(strip, chart, j);
}

/** "2nd", "3rd", "20th": a place in the listing, for the tiles and the summary. */
export function ordinal(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th";
  return `${n}${suffix}`;
}

/** Where a chart sits, in words: its place when the listing is full, or "last". */
export function placeLabel(strip: string[], chart: string): string {
  const i = strip.indexOf(chart);
  const last = strip.slice(i + 1).every(isChart);
  return last ? "last" : ordinal(i + 1);
}
