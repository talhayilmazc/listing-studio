// A design adjusted on one mockup, its look and recoloured parts (seller feedback, Part B): the
// editor's copy of backend pipeline/design_edits.py. The same arithmetic, so the preview and the
// rendered mockup agree; both read backend/tests/fixtures/design_edit_cases.json.
//
// Every edit is a render-time layer: the uploaded design file is never changed.
import type { Pt, Quad } from "./areaEditor";

// The projective maps (the same as lib/areaEditor.ts's; kept here so this module imports no
// values and runs as it is under the tests' node).
function homography(from: Pt[], to: Pt[]): number[] {
  const a: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = from[i];
    const [u, v] = to[i];
    a.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    b.push(u);
    a.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    b.push(v);
  }
  const n = 8;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(m[r][c]) > Math.abs(m[p][c])) p = r;
    [m[c], m[p]] = [m[p], m[c]];
    const d = m[c][c] || 1e-12;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = m[r][c] / d;
      for (let k = c; k <= n; k++) m[r][k] -= f * m[c][k];
    }
  }
  return [...m.map((row, i) => row[n] / (row[i] || 1e-12)), 1];
}

function apply(h: number[], [x, y]: Pt): Pt {
  const w = h[6] * x + h[7] * y + h[8];
  return [(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w];
}

export type Adjust = {
  dx: number; dy: number; scale: number; sx: number; sy: number; angle: number;
  opacity: number; brightness: number; contrast: number;
  /** Each corner moved on its own (perspective), shares of the area; null: none. */
  corners: [number, number][] | null;
};
export type Region =
  | { kind: "rect" | "lasso"; points: [number, number][]; colour: string }
  | { kind: "wand"; seed: [number, number]; tolerance: number; colour: string };

export const NEUTRAL: Adjust = { dx: 0, dy: 0, scale: 1, sx: 1, sy: 1, angle: 0, opacity: 1, brightness: 0, contrast: 0, corners: null };
export const RANGES: Record<Exclude<keyof Adjust, "corners">, [number, number]> = {
  dx: [-1.5, 1.5], dy: [-1.5, 1.5], scale: [0.1, 4], sx: [0.2, 5], sy: [0.2, 5], angle: [-180, 180],
  opacity: [0.3, 1], brightness: [-0.25, 0.25], contrast: [-0.25, 0.25],
};
export const VISIBLE_ALPHA = 8;
const FIELDS = Object.keys(RANGES) as (keyof typeof RANGES)[];

const round5 = (v: number) => Math.round(v * 1e5) / 1e5;
const clip = (k: keyof typeof RANGES, v: number) => round5(Math.min(RANGES[k][1], Math.max(RANGES[k][0], v)));

export function full(a: Partial<Adjust> | null | undefined): Adjust {
  return { ...NEUTRAL, ...(a ?? {}) } as Adjust;
}

/** As stored: each field inside its range; null when it is the automatic fit with the neutral look. */
export function clean(a: Partial<Adjust> | null | undefined): Adjust | null {
  if (!a) return null;
  const out = full(a);
  for (const k of FIELDS) out[k] = clip(k, Number(out[k]));
  const c = out.corners?.map(([x, y]) => [round5(Math.min(1, Math.max(-1, x))), round5(Math.min(1, Math.max(-1, y)))] as [number, number]) ?? null;
  out.corners = c && c.some(([x, y]) => Math.abs(x) > 1e-6 || Math.abs(y) > 1e-6) ? c : null;
  const neutral = FIELDS.every((k) => Math.abs(out[k] - NEUTRAL[k]) < 1e-9) && out.corners === null;
  return neutral ? null : out;
}

/** The change from one adjustment to another (added moves, turns and look; scale factors; corner offsets). */
export function delta(before: Partial<Adjust> | null, after: Partial<Adjust> | null): Adjust {
  const b = full(before);
  const a = full(after);
  const bc = b.corners ?? [[0, 0], [0, 0], [0, 0], [0, 0]];
  const ac = a.corners ?? [[0, 0], [0, 0], [0, 0], [0, 0]];
  return {
    dx: a.dx - b.dx, dy: a.dy - b.dy, angle: a.angle - b.angle,
    opacity: a.opacity - b.opacity, brightness: a.brightness - b.brightness, contrast: a.contrast - b.contrast,
    scale: b.scale ? a.scale / b.scale : 1, sx: b.sx ? a.sx / b.sx : 1, sy: b.sy ? a.sy / b.sy : 1,
    corners: ac.map(([x, y], i) => [x - bc[i][0], y - bc[i][1]] as [number, number]),
  };
}

/** A change on top of an adjustment: each design keeps its own fit and moves by the same amount. */
export function compose(base: Partial<Adjust> | null, change: Adjust): Adjust | null {
  const b = full(base);
  const bc = b.corners ?? [[0, 0], [0, 0], [0, 0], [0, 0]];
  const dc = change.corners ?? [[0, 0], [0, 0], [0, 0], [0, 0]];
  let angle = b.angle + change.angle;
  if (angle > 180) angle -= 360;
  else if (angle < -180) angle += 360;
  return clean({
    dx: b.dx + change.dx, dy: b.dy + change.dy, angle,
    opacity: b.opacity + change.opacity, brightness: b.brightness + change.brightness, contrast: b.contrast + change.contrast,
    scale: b.scale * change.scale, sx: b.sx * change.sx, sy: b.sy * change.sy,
    corners: bc.map(([x, y], i) => [x + dc[i][0], y + dc[i][1]] as [number, number]),
  });
}

const dist = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** The automatic fit (mockups.place): [u0, v0, du, dv, areaW, areaH]. */
export function fitBox(area: Quad, dw: number, dh: number, size: number): [number, number, number, number, number, number] {
  const [tl, tr, br, bl] = area;
  const areaW = (dist(tl, tr) + dist(bl, br)) / 2;
  const areaH = (dist(tl, bl) + dist(tr, br)) / 2;
  const scale = Math.min(areaW / dw, areaH / dh) * size;
  const du = (dw * scale) / areaW;
  const dv = (dh * scale) / areaH;
  return [(1 - du) / 2, 0, du, dv, areaW, areaH];
}

/** The design's corners (TL, TR, BR, BL) in the area's unit square, after the adjustment. */
export function unitPoints(area: Quad, dw: number, dh: number, size: number, adjust: Partial<Adjust> | null): Pt[] {
  const [u0, v0, du, dv, areaW, areaH] = fitBox(area, dw, dh, size);
  const a = full(adjust);
  const cu = u0 + du / 2 + a.dx;
  const cv = v0 + dv / 2 + a.dy;
  const hw = ((du * areaW) / 2) * a.scale * a.sx;
  const hh = ((dv * areaH) / 2) * a.scale * a.sy;
  const t = (a.angle * Math.PI) / 180;
  const cos = Math.cos(t);
  const sin = Math.sin(t);
  const out = ([[-1, -1], [1, -1], [1, 1], [-1, 1]] as const).map(([sx, sy]): Pt => {
    const x = sx * hw;
    const y = sy * hh;
    return [cu + (x * cos - y * sin) / areaW, cv + (x * sin + y * cos) / areaH];
  });
  return a.corners ? out.map(([u, v], i): Pt => [u + a.corners![i][0], v + a.corners![i][1]]) : out;
}

/** The design's corners in photo pixels (the unit points through the area's perspective). */
export function photoPoints(area: Quad, dw: number, dh: number, size: number, adjust: Partial<Adjust> | null): Quad {
  const h = homography([[0, 0], [1, 0], [1, 1], [0, 1]], area);
  return unitPoints(area, dw, dh, size, adjust).map((p) => apply(h, p)) as Quad;
}

/** The unit-square point of a photo point (to turn a drag into an adjustment). */
export function toUnit(area: Quad, p: Pt): Pt {
  return apply(homography(area, [[0, 0], [1, 0], [1, 1], [0, 1]]), p);
}

// --- the look --------------------------------------------------------------------------------------

/** The look on RGBA pixels, in place: (c - 0.5)(1 + contrast) + 0.5 + brightness, alpha × opacity. */
export function lookPixels(px: Uint8ClampedArray | Uint8Array, adjust: Partial<Adjust> | null): void {
  const a = full(adjust);
  if (Math.abs(a.brightness) < 1e-9 && Math.abs(a.contrast) < 1e-9 && Math.abs(a.opacity - 1) < 1e-9) return;
  for (let i = 0; i < px.length; i += 4) {
    for (let c = 0; c < 3; c++) {
      const v = Math.min(1, Math.max(0, (px[i + c] / 255 - 0.5) * (1 + a.contrast) + 0.5 + a.brightness));
      px[i + c] = Math.floor(v * 255 + 0.5);
    }
    px[i + 3] = Math.floor(px[i + 3] * a.opacity + 0.5);
  }
}

// --- recolouring ---------------------------------------------------------------------------------

function rectMask(points: [number, number][], w: number, h: number, out: Uint8Array): void {
  const [[x0, y0], [x1, y1]] = points;
  const lx = Math.min(x0, x1), hx = Math.max(x0, x1), ly = Math.min(y0, y1), hy = Math.max(y0, y1);
  for (let y = 0; y < h; y++) {
    const cy = (y + 0.5) / h;
    if (cy < ly || cy > hy) continue;
    for (let x = 0; x < w; x++) {
      const cx = (x + 0.5) / w;
      if (cx >= lx && cx <= hx) out[y * w + x] = 1;
    }
  }
}

function lassoMask(points: [number, number][], w: number, h: number, out: Uint8Array): void {
  const n = points.length;
  for (let y = 0; y < h; y++) {
    const cy = (y + 0.5) / h;
    const xs: number[] = [];
    for (let i = 0; i < n; i++) {
      const [x1, y1] = points[i];
      const [x2, y2] = points[(i + 1) % n];
      if ((y1 <= cy && cy < y2) || (y2 <= cy && cy < y1)) xs.push(x1 + ((cy - y1) * (x2 - x1)) / (y2 - y1));
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const start = Math.max(0, Math.ceil(xs[k] * w - 0.5));
      const end = Math.min(w, Math.ceil(xs[k + 1] * w - 0.5));
      for (let x = start; x < end; x++) out[y * w + x] = 1;
    }
  }
}

function wandMask(px: Uint8ClampedArray | Uint8Array, w: number, h: number, seed: [number, number], tolerance: number, out: Uint8Array): void {
  const sx = Math.min(w - 1, Math.floor(seed[0] * w));
  const sy = Math.min(h - 1, Math.floor(seed[1] * h));
  const s = (sy * w + sx) * 4;
  if (px[s + 3] <= VISIBLE_ALPHA) return;
  const limit = Math.round(tolerance * 2.55);
  const similar = (i: number) => {
    const o = i * 4;
    return px[o + 3] > VISIBLE_ALPHA && Math.abs(px[o] - px[s]) <= limit && Math.abs(px[o + 1] - px[s + 1]) <= limit
      && Math.abs(px[o + 2] - px[s + 2]) <= limit;
  };
  const stack = [sy * w + sx];
  out[sy * w + sx] = 1;
  while (stack.length) {
    const i = stack.pop()!;
    const x = i % w;
    const y = (i - x) / w;
    const next = [x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1, y > 0 ? i - w : -1, y < h - 1 ? i + w : -1];
    for (const j of next) {
      if (j >= 0 && !out[j] && similar(j)) {
        out[j] = 1;
        stack.push(j);
      }
    }
  }
}

/** Which pixels a region selects (1 = selected), on the pixels as earlier regions left them. */
export function regionMask(px: Uint8ClampedArray | Uint8Array, w: number, h: number, region: Region): Uint8Array {
  const out = new Uint8Array(w * h);
  if (region.kind === "rect") rectMask(region.points, w, h, out);
  else if (region.kind === "lasso") lassoMask(region.points, w, h, out);
  else if (region.kind === "wand") wandMask(px, w, h, region.seed, region.tolerance, out);
  return out;
}

/** Recolour in place: each region's pixels take its colour, their alpha (so the soft edges) kept. */
export function recolourPixels(px: Uint8ClampedArray | Uint8Array, w: number, h: number, regions: Region[] | null | undefined): void {
  for (const region of regions ?? []) {
    const mask = regionMask(px, w, h, region);
    const r = parseInt(region.colour.slice(1, 3), 16);
    const g = parseInt(region.colour.slice(3, 5), 16);
    const b = parseInt(region.colour.slice(5, 7), 16);
    for (let i = 0; i < w * h; i++) {
      if (mask[i] && px[i * 4 + 3] > 0) {
        px[i * 4] = r;
        px[i * 4 + 1] = g;
        px[i * 4 + 2] = b;
      }
    }
  }
}

/** "Apply this change to…": the three scopes, in the seller's words. */
export const APPLY_SCOPES = {
  design_photos: "This design on all photos",
  photo_designs: "All designs on this photo",
  template_all: "All designs on all photos of this template",
} as const;
export type ApplyScope = keyof typeof APPLY_SCOPES;

/** The look's sliders: label, field, step. */
export const LOOK_SLIDERS = [
  { key: "opacity", label: "Opacity", step: 0.01 },
  { key: "brightness", label: "Brightness", step: 0.01 },
  { key: "contrast", label: "Contrast", step: 0.01 },
] as const;
