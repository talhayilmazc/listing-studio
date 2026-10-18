/**
 * The print-area editor's geometry (components/PrintAreaEditor.tsx): moving, resizing,
 * rotating and free corners, smart guides with snapping, undo and redo, and the live
 * preview's placement. Pure functions in the photo's own pixels, so a rectangle stays a
 * rectangle whatever the photo's proportions. lib/areaEditor.test.ts tests each one.
 *
 * Nothing here talks to the server; the page saves the result (backend api/mockups.py).
 */

export type Pt = [number, number];
/** Corners TL, TR, BR, BL. */
export type Quad = [Pt, Pt, Pt, Pt];

/** A rotated rectangle: centre, width, height and the angle of its top edge (radians). */
export interface Rect {
  cx: number;
  cy: number;
  w: number;
  h: number;
  angle: number;
}

/** The smallest area the editor makes (photo pixels), so a corner cannot cross its opposite. */
export const MIN_SIZE = 12;
/** Snapping reaches this far, in screen pixels (the editor turns it into photo pixels). */
export const SNAP_SCREEN_PX = 8;
/** An angle this close (degrees) to upright, or to the garment's axis, snaps to it. */
export const SNAP_DEGREES = 2;

const add = (a: Pt, b: Pt): Pt => [a[0] + b[0], a[1] + b[1]];
const sub = (a: Pt, b: Pt): Pt => [a[0] - b[0], a[1] - b[1]];
const mul = (a: Pt, k: number): Pt => [a[0] * k, a[1] * k];
const dot = (a: Pt, b: Pt) => a[0] * b[0] + a[1] * b[1];
const len = (a: Pt) => Math.hypot(a[0], a[1]);
const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

/** Shares of the photo (what the server keeps) to photo pixels, and back (4 decimals, inside the photo). */
export function toPx(quad: number[][], width: number, height: number): Quad {
  return quad.map(([x, y]) => [x * width, y * height]) as Quad;
}
export function toShares(quad: Quad, width: number, height: number): [number, number][] {
  const r = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
  return quad.map(([x, y]) => [r(x / width), r(y / height)]);
}

export function centre(q: Quad): Pt {
  return [(q[0][0] + q[1][0] + q[2][0] + q[3][0]) / 4, (q[0][1] + q[1][1] + q[2][1] + q[3][1]) / 4];
}

export function rectOf(q: Quad): Rect {
  const top = sub(q[1], q[0]);
  const angle = Math.atan2(top[1], top[0]);
  const w = (len(sub(q[1], q[0])) + len(sub(q[2], q[3]))) / 2;
  const h = (len(sub(q[3], q[0])) + len(sub(q[2], q[1]))) / 2;
  const [cx, cy] = centre(q);
  return { cx, cy, w, h, angle };
}

export function quadOf(r: Rect): Quad {
  const across: Pt = [Math.cos(r.angle), Math.sin(r.angle)];
  const down: Pt = [-across[1], across[0]];
  const c: Pt = [r.cx, r.cy];
  const at = (u: number, v: number) => add(c, add(mul(across, u * r.w / 2), mul(down, v * r.h / 2)));
  return [at(-1, -1), at(1, -1), at(1, 1), at(-1, 1)];
}

/** Whether the quad is a (rotated) rectangle: square corners, equal opposite sides. */
export function isRect(q: Quad, tolerance = 0.03): boolean {
  for (let i = 0; i < 4; i++) {
    const a = sub(q[(i + 1) % 4], q[i]);
    const b = sub(q[(i + 3) % 4], q[i]);
    if (Math.abs(dot(a, b)) / Math.max(len(a) * len(b), 1e-9) > tolerance) return false;
  }
  const r = rectOf(q);
  return Math.abs(len(sub(q[1], q[0])) - len(sub(q[2], q[3]))) <= tolerance * r.w
    && Math.abs(len(sub(q[3], q[0])) - len(sub(q[2], q[1]))) <= tolerance * r.h;
}

export function move(q: Quad, dx: number, dy: number): Quad {
  return q.map(([x, y]) => [x + dx, y + dy]) as Quad;
}

/** A corner dragged to ``p`` keeping the area a rectangle at its angle: the opposite corner stays put. */
export function resizeRect(q: Quad, corner: number, p: Pt, min = MIN_SIZE): Quad {
  const r = rectOf(q);
  const across: Pt = [Math.cos(r.angle), Math.sin(r.angle)];
  const down: Pt = [-across[1], across[0]];
  const opposite = quadOf(r)[(corner + 2) % 4];
  const su = corner === 1 || corner === 2 ? 1 : -1;
  const sv = corner === 2 || corner === 3 ? 1 : -1;
  const d = sub(p, opposite);
  const w = Math.max(min, su * dot(d, across));
  const h = Math.max(min, sv * dot(d, down));
  const moved = add(opposite, add(mul(across, su * w), mul(down, sv * h)));
  const [cx, cy] = mid(opposite, moved);
  return quadOf({ cx, cy, w, h, angle: r.angle });
}

/** "Free corners": one corner moves, the others stay (perspective). */
export function dragCorner(q: Quad, corner: number, p: Pt): Quad {
  return q.map((c, i) => (i === corner ? [p[0], p[1]] : c)) as Quad;
}

/** The area turned by ``by`` radians about its centre. */
export function rotate(q: Quad, by: number): Quad {
  const c = centre(q);
  const cos = Math.cos(by);
  const sin = Math.sin(by);
  return q.map((p) => {
    const d = sub(p, c);
    return [c[0] + d[0] * cos - d[1] * sin, c[1] + d[0] * sin + d[1] * cos];
  }) as Quad;
}

/** Where the rotate handle sits: above the middle of the top edge, ``gap`` photo pixels out. */
export function rotateHandle(q: Quad, gap: number): Pt {
  const top = mid(q[0], q[1]);
  const r = rectOf(q);
  const up: Pt = [Math.sin(r.angle), -Math.cos(r.angle)];
  return add(top, mul(up, gap));
}

/** The turn from where the rotate drag began to where the pointer is now (about the centre). */
export function turn(q: Quad, from: Pt, to: Pt): number {
  const c = centre(q);
  const a = Math.atan2(from[1] - c[1], from[0] - c[0]);
  const b = Math.atan2(to[1] - c[1], to[0] - c[0]);
  return b - a;
}

// --- smart guides ------------------------------------------------------------------------------

/** A feature of an area that can line up with a guide. */
export type Feature = "centre" | "top" | "bottom" | "left" | "right";

export interface Guide {
  /** Two points on the line. */
  a: Pt;
  b: Pt;
  /** "axis": the garment's centre axis; "collar": its collar line plus the standard offset
   *  (the suggestion's top); "area": another area's edge or centre; "photo": the photo's centre. */
  kind: "axis" | "collar" | "area" | "photo";
  /** Upright-ish ("v") or level-ish ("h"): which of the area's features it lines up with. */
  dir: "v" | "h";
  /** The features of the moving area this guide snaps. */
  features: Feature[];
}

export function features(q: Quad): Record<Feature, Pt> {
  return {
    centre: centre(q),
    top: mid(q[0], q[1]),
    bottom: mid(q[3], q[2]),
    left: mid(q[0], q[3]),
    right: mid(q[1], q[2]),
  };
}

const V_FEATURES: Feature[] = ["left", "centre", "right"];
const H_FEATURES: Feature[] = ["top", "centre", "bottom"];

/**
 * Every guide for the area being edited: the photo's centre lines; its garment's centre
 * axis and collar line (from the suggestion for that garment); and the edges and centre
 * lines of the photo's other areas (so tops or bottoms line up across garments).
 */
export function makeGuides(width: number, height: number, suggestion: Quad | null, others: Quad[]): Guide[] {
  // In order of preference when two are as close: the garment's own lines, the other
  // areas, then the photo's centre (a tee in the middle of the photo has both).
  const guides: Guide[] = [];
  if (suggestion) {
    const f = features(suggestion);
    guides.push({ a: f.top, b: f.bottom, kind: "axis", dir: "v", features: ["centre"] });
    guides.push({ a: suggestion[0], b: suggestion[1], kind: "collar", dir: "h", features: ["top"] });
  }
  for (const o of others) {
    const r = rectOf(o);
    const across: Pt = [Math.cos(r.angle), Math.sin(r.angle)];
    const down: Pt = [-across[1], across[0]];
    const f = features(o);
    for (const p of [f.top, f.centre, f.bottom]) guides.push({ a: p, b: add(p, across), kind: "area", dir: "h", features: H_FEATURES });
    for (const p of [f.left, f.centre, f.right]) guides.push({ a: p, b: add(p, down), kind: "area", dir: "v", features: V_FEATURES });
  }
  guides.push({ a: [width / 2, 0], b: [width / 2, height], kind: "photo", dir: "v", features: ["centre"] });
  guides.push({ a: [0, height / 2], b: [width, height / 2], kind: "photo", dir: "h", features: ["centre"] });
  return guides;
}

/** Signed distance of ``p`` from the line, and the line's unit normal. */
function offset(p: Pt, g: Guide): { d: number; n: Pt } {
  const t = sub(g.b, g.a);
  const l = Math.max(len(t), 1e-9);
  const n: Pt = [-t[1] / l, t[0] / l];
  return { d: dot(sub(p, g.a), n), n };
}

export interface Snapped<T> {
  value: T;
  /** The guides it lined up with (drawn while dragging). */
  active: Guide[];
}

/** The area moved onto the nearest guide in each direction within ``reach`` (photo pixels). */
export function snapArea(q: Quad, guides: Guide[], reach: number): Snapped<Quad> {
  let out = q;
  const active: Guide[] = [];
  for (const dir of ["v", "h"] as const) {
    const f = features(out);
    let best: { d: number; n: Pt; g: Guide } | null = null;
    for (const g of guides) {
      if (g.dir !== dir) continue;
      for (const name of g.features) {
        const { d, n } = offset(f[name], g);
        if (Math.abs(d) <= reach && (!best || Math.abs(d) < Math.abs(best.d))) best = { d, n, g };
      }
    }
    if (best) {
      out = move(out, -best.n[0] * best.d, -best.n[1] * best.d);
      active.push(best.g);
    }
  }
  return { value: out, active };
}

/** A dragged corner (resizing, or a free corner) onto the nearest guide in each direction. */
export function snapPoint(p: Pt, guides: Guide[], reach: number): Snapped<Pt> {
  let out = p;
  const active: Guide[] = [];
  for (const dir of ["v", "h"] as const) {
    let best: { d: number; n: Pt; g: Guide } | null = null;
    for (const g of guides) {
      if (g.dir !== dir) continue;
      const { d, n } = offset(out, g);
      if (Math.abs(d) <= reach && (!best || Math.abs(d) < Math.abs(best.d))) best = { d, n, g };
    }
    if (best) {
      out = [out[0] - best.n[0] * best.d, out[1] - best.n[1] * best.d];
      active.push(best.g);
    }
  }
  return { value: out, active };
}

/** An angle snapped to upright, or to the garment's axis, within ``SNAP_DEGREES``. */
export function snapAngle(angle: number, axis: number | null, degrees = SNAP_DEGREES): { angle: number; snapped: "upright" | "axis" | null } {
  const near = (a: number, b: number) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b))) <= (degrees * Math.PI) / 180;
  if (near(angle, 0)) return { angle: 0, snapped: "upright" };
  if (axis !== null && near(angle, axis)) return { angle: axis, snapped: "axis" };
  return { angle, snapped: null };
}

/** The angle a suggestion's top edge makes: the garment's own axis, for snapping a rotation. */
export function axisAngle(suggestion: Quad | null): number | null {
  return suggestion ? rectOf(suggestion).angle : null;
}

/** The garment's axis to snap a rotation to: the analysis's own estimate (degrees, from the
 *  server; it may lean even when the suggestion was left upright), else the suggestion's angle. */
export function garmentAxis(estimateDegrees: number | null | undefined, suggestion: Quad | null): number | null {
  if (typeof estimateDegrees === "number" && Number.isFinite(estimateDegrees)) return (estimateDegrees * Math.PI) / 180;
  return axisAngle(suggestion);
}

/** The area's angle in degrees (its top edge, clockwise; -180..180), to one decimal. */
export function angleDegrees(q: Quad): number {
  const d = (rectOf(q).angle * 180) / Math.PI;
  const r = Math.round(d * 10) / 10;
  return Object.is(r, -0) ? 0 : r;
}

/** The area turned to exactly ``angle`` radians about its centre ("Match garment angle"). */
export function turnTo(q: Quad, angle: number): Quad {
  return rotate(q, angle - rectOf(q).angle);
}

/** "[" and "]" turn the area by 1 degree, with Shift by 5 (radians; by the key's code, so
 *  Shift, which makes them "{" and "}" on many keyboards, does not change the key). */
export function angleKey(code: string, shift: boolean): number | null {
  const step = ((shift ? 5 : 1) * Math.PI) / 180;
  return code === "BracketLeft" ? -step : code === "BracketRight" ? step : null;
}

/** The guide drawn when a turn snaps to the garment's axis: along the axis, through the area's centre. */
export function axisGuide(q: Quad, axis: number): Guide {
  const c = centre(q);
  return { a: c, b: add(c, [-Math.sin(axis), Math.cos(axis)]), kind: "axis", dir: "v", features: ["centre"] };
}

/** "Copy this area to all garments": the area placed on another garment where it sits on its
 *  own (relative to each garment's suggestion), so the copy scales with that garment's width. */
export function copyToGarment(area: Quad, from: Quad, to: Quad): Quad {
  const h = homography(from, to);
  return area.map((p) => apply(h, p)) as Quad;
}

/** The ends of a guide line drawn across the whole photo. */
export function across(g: Guide, width: number, height: number): [Pt, Pt] {
  const t = sub(g.b, g.a);
  const l = Math.max(len(t), 1e-9);
  const far = (width + height) * 2;
  const u = mul(t, far / l);
  return [sub(g.a, u), add(g.a, u)];
}

/** One key press: the arrow keys nudge (Shift: a bigger step), in photo pixels. */
export function nudge(key: string, shift: boolean, width: number): Pt | null {
  const step = Math.max(1, Math.round(width * (shift ? 0.02 : 0.002)));
  return ({ ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] } as Record<string, Pt>)[key] ?? null;
}

// --- undo and redo -----------------------------------------------------------------------------

export interface History<T> {
  past: T[];
  present: T;
  future: T[];
}

export const LIMIT = 100;

export function history<T>(present: T): History<T> {
  return { past: [], present, future: [] };
}

/** A new state: the old one can be undone; anything undone before is gone. */
export function record<T>(h: History<T>, next: T): History<T> {
  if (JSON.stringify(next) === JSON.stringify(h.present)) return h;
  return { past: [...h.past, h.present].slice(-LIMIT), present: next, future: [] };
}

export function undo<T>(h: History<T>): History<T> {
  if (!h.past.length) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function redo<T>(h: History<T>): History<T> {
  if (!h.future.length) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

// --- the live preview --------------------------------------------------------------------------

/**
 * Where the design sits inside the area, as the mockups place it (backend pipeline/mockups.py
 * ``place``): its proportions kept, ``size`` of the area, centred and top-aligned. Returned
 * as the design's four corners in photo pixels (following the area's perspective).
 */
/** The print area's width and height as the renderer measures them (pipeline/mockups.py
 * ``place``): the mean of its top and bottom edges, and of its left and right sides. */
export function areaSize(q: Quad): { w: number; h: number } {
  const d = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  return { w: (d(q[0], q[1]) + d(q[3], q[2])) / 2, h: (d(q[0], q[3]) + d(q[1], q[2])) / 2 };
}

/** Where the design lands in the area: aspect kept, ``size`` of the area on its limiting
 * side (1: it touches the area there; a design of the area's proportions fills it corner to
 * corner), top-aligned, centred. The same arithmetic as the renderer (pipeline/mockups.py
 * ``place``), so the preview and the mockup put it in the same place; ``designW``/``designH``
 * are the design's trimmed size (its transparent padding left out). */
export function designQuad(q: Quad, designW: number, designH: number, size: number): Quad {
  const r = areaSize(q);
  const s = size * Math.min(r.w / designW, r.h / designH);
  const bw = (designW * s) / r.w;
  const bh = (designH * s) / r.h;
  const u0 = (1 - bw) / 2;
  const h = homography([[0, 0], [1, 0], [1, 1], [0, 1]], q);
  const at = (u: number, v: number) => apply(h, [u, v]);
  return [at(u0, 0), at(u0 + bw, 0), at(u0 + bw, bh), at(u0, bh)];
}

/** "Fit area to design": the area takes the design's proportions with its top edge (and so
 * its top-centre) fixed: its sides grow or shrink to the height the width calls for; when that
 * would leave the photo (``maxW``/``maxH``), the width changes instead, around the top-centre. */
export function fitAreaToDesign(q: Quad, designW: number, designH: number, maxW = Infinity, maxH = Infinity): Quad {
  const r = areaSize(q);
  if (r.w <= 0 || r.h <= 0 || designW <= 0 || designH <= 0) return q;
  const k = (r.w * designH) / designW / r.h;
  const [tl, tr, br, bl] = q;
  const taller: Quad = [tl, tr, [tr[0] + (br[0] - tr[0]) * k, tr[1] + (br[1] - tr[1]) * k], [tl[0] + (bl[0] - tl[0]) * k, tl[1] + (bl[1] - tl[1]) * k]];
  const inside = taller.every(([x, y]) => x >= -0.5 && y >= -0.5 && x <= maxW + 0.5 && y <= maxH + 0.5);
  if (inside) return taller;
  // Keep the height; the width follows the design, the top-centre stays where it is.
  const j = (r.h * designW) / designH / r.w;
  const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const around = (c: Pt, p: Pt): Pt => [c[0] + (p[0] - c[0]) * j, c[1] + (p[1] - c[1]) * j];
  const top = mid(tl, tr);
  const bottom = mid(bl, br);
  return [around(top, tl), around(top, tr), around(bottom, br), around(bottom, bl)];
}

/** The projective map taking four points to four points (3x3, row-major, h33 = 1). */
export function homography(from: Pt[], to: Pt[]): number[] {
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
  const h = solve(a, b);
  return [...h, 1];
}

export function apply(h: number[], [x, y]: Pt): Pt {
  const w = h[6] * x + h[7] * y + h[8];
  return [(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w];
}

/** A CSS ``matrix3d`` that maps an element of ``w`` x ``h`` (origin top left) onto ``quad``. */
export function matrix3d(w: number, h: number, quad: Quad): string {
  const m = homography([[0, 0], [w, 0], [w, h], [0, h]], quad);
  // Column-major 4x4 for CSS, with z left alone.
  const css = [m[0], m[3], 0, m[6], m[1], m[4], 0, m[7], 0, 0, 1, 0, m[2], m[5], 0, m[8]];
  return `matrix3d(${css.map((v) => Number(v.toFixed(10))).join(",")})`;
}

function solve(a: number[][], b: number[]): number[] {
  const n = b.length;
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
  return m.map((row, i) => row[n] / (row[i] || 1e-12));
}
