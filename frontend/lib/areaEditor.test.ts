// The print-area editor's geometry (lib/areaEditor.ts, components/PrintAreaEditor.tsx):
// moving the whole area, resizing it as a rectangle, free corners, rotating, snapping to
// each kind of guide (the garment's axis, its collar line, another area's edges, the
// photo's centre), snapping off, undo and redo, and the live preview's placement.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  apply,
  axisAngle,
  designQuad,
  dragCorner,
  history,
  homography,
  isRect,
  makeGuides,
  move,
  nudge,
  quadOf,
  record,
  rectOf,
  redo,
  resizeRect,
  rotate,
  snapAngle,
  snapArea,
  snapPoint,
  toPx,
  toShares,
  turn,
  undo,
  type Quad,
} from "./areaEditor.ts";

const W = 1000;
const H = 1200;
const box = (x0: number, y0: number, x1: number, y1: number): Quad => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const close = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);
const closeQuad = (a: Quad, b: Quad, eps = 1e-6) => a.forEach((p, i) => { close(p[0], b[i][0], eps); close(p[1], b[i][1], eps); });

test("moving the whole area moves every corner by the same amount", () => {
  const q = box(300, 200, 700, 680);
  closeQuad(move(q, 25, -10), box(325, 190, 725, 670));
});

test("resizing by a corner keeps the area a rectangle and the opposite corner where it was", () => {
  const q = box(300, 200, 700, 680);
  const bigger = resizeRect(q, 2, [760, 800]);
  closeQuad(bigger, box(300, 200, 760, 800));
  // Rotated: still a rectangle at the same angle, the opposite corner fixed.
  const turned = rotate(q, 0.3);
  const resized = resizeRect(turned, 0, [turned[0][0] - 30, turned[0][1] - 10]);
  assert.ok(isRect(resized));
  close(rectOf(resized).angle, rectOf(turned).angle);
  closeQuad([resized[2], resized[2], resized[2], resized[2]], [turned[2], turned[2], turned[2], turned[2]]);
  // A corner cannot cross its opposite: the area keeps a minimum size.
  const tiny = resizeRect(q, 2, [100, 100]);
  assert.ok(rectOf(tiny).w >= 12 && rectOf(tiny).h >= 12);
});

test("free corners move one corner only (perspective)", () => {
  const q = box(300, 200, 700, 680);
  const free = dragCorner(q, 1, [720, 230]);
  assert.deepEqual(free[1], [720, 230]);
  assert.deepEqual([free[0], free[2], free[3]], [q[0], q[2], q[3]]);
  assert.equal(isRect(free), false);
});

test("rotating keeps the centre and the size; the drag's turn is measured about the centre", () => {
  const q = box(300, 200, 700, 680);
  const r = rectOf(q);
  const turned = rotate(q, Math.PI / 12);
  close(rectOf(turned).cx, r.cx);
  close(rectOf(turned).cy, r.cy);
  close(rectOf(turned).w, r.w);
  close(rectOf(turned).h, r.h);
  close(rectOf(turned).angle, Math.PI / 12);
  close(turn(q, [500, 100], [600, 440]), Math.PI / 2);
});

test("rect and quad round-trip, and shares of the photo round-trip to pixels", () => {
  const r = { cx: 500, cy: 400, w: 320, h: 380, angle: 0.2 };
  const back = rectOf(quadOf(r));
  for (const k of ["cx", "cy", "w", "h", "angle"] as const) close(back[k], r[k]);
  const shares = toShares(box(300, 240, 700, 720), W, H);
  assert.deepEqual(shares, [[0.3, 0.2], [0.7, 0.2], [0.7, 0.6], [0.3, 0.6]]);
  closeQuad(toPx(shares, W, H), box(300, 240, 700, 720));
});

test("snaps the area's centre onto the garment's centre axis", () => {
  const suggestion = box(380, 300, 640, 620); // its axis: x = 510
  const guides = makeGuides(W, H, suggestion, []);
  const q = box(386, 340, 646, 660); // centre x = 516: 6 px off
  const { value, active } = snapArea(q, guides, 8);
  close(rectOf(value).cx, 510);
  assert.ok(active.some((g) => g.kind === "axis"));
  // Out of reach: not moved.
  const far = snapArea(box(400, 340, 660, 660), guides, 8);
  assert.ok(!far.active.some((g) => g.kind === "axis"));
});

test("snaps the area's top onto the collar line plus the standard offset (the suggestion's top)", () => {
  const suggestion = box(380, 300, 640, 620);
  const guides = makeGuides(W, H, suggestion, []);
  const { value, active } = snapArea(box(200, 305, 400, 560), guides, 8);
  close(value[0][1], 300);
  assert.ok(active.some((g) => g.kind === "collar"));
});

test("snaps an edge onto another area's edge on the same photo (bottoms line up)", () => {
  const other = box(80, 300, 280, 620);
  const guides = makeGuides(W, H, null, [other]);
  const { value, active } = snapArea(box(700, 330, 900, 625), guides, 8); // bottom 625 -> 620
  close(value[2][1], 620);
  assert.ok(active.some((g) => g.kind === "area" && g.dir === "h"));
  // Left edges too.
  const left = snapArea(box(85, 700, 285, 900), guides, 8);
  close(left.value[0][0], 80);
});

test("snaps the area's centre onto the photo's centre lines", () => {
  const guides = makeGuides(W, H, null, []);
  const { value, active } = snapArea(box(296, 395, 696, 795), guides, 8); // centre (496, 595)
  close(rectOf(value).cx, 500);
  close(rectOf(value).cy, 600);
  assert.equal(active.filter((g) => g.kind === "photo").length, 2);
});

test("a dragged corner snaps too, and with snapping off nothing moves", () => {
  const guides = makeGuides(W, H, null, [box(80, 300, 280, 620)]);
  const p = snapPoint([704, 616], guides, 8);
  assert.deepEqual(p.value.map(Math.round), [704, 620]);
  // Snapping off (Alt held, or the switch on a phone): the editor passes reach 0.
  const off = snapArea(box(296, 395, 696, 795), guides, 0);
  closeQuad(off.value, box(296, 395, 696, 795));
  assert.equal(off.active.length, 0);
});

test("a rotation snaps to upright and to the garment's axis", () => {
  assert.deepEqual(snapAngle(0.02, null), { angle: 0, snapped: "upright" });
  const axis = axisAngle(rotate(box(380, 300, 640, 620), 0.2));
  assert.equal(snapAngle(0.21, axis).snapped, "axis");
  assert.equal(snapAngle(0.4, axis).snapped, null);
});

test("undo and redo step through the edits; a new edit drops what was undone", () => {
  let h = history([box(0, 0, 10, 10)]);
  h = record(h, [box(5, 5, 15, 15)]);
  h = record(h, [box(9, 9, 19, 19)]);
  h = undo(h);
  assert.deepEqual(h.present, [box(5, 5, 15, 15)]);
  h = redo(h);
  assert.deepEqual(h.present, [box(9, 9, 19, 19)]);
  h = undo(undo(h));
  assert.deepEqual(h.present, [box(0, 0, 10, 10)]);
  h = record(h, [box(1, 1, 2, 2)]);
  assert.equal(h.future.length, 0);
  assert.deepEqual(undo(undo(undo(h))).present, [box(0, 0, 10, 10)]); // nothing before the first state
  assert.equal(record(h, h.present), h); // the same state is not a new step
});

test("arrow keys nudge; Shift is a bigger step", () => {
  assert.deepEqual(nudge("ArrowLeft", false, 1000), [-2, 0]);
  assert.deepEqual(nudge("ArrowDown", true, 1000), [0, 20]);
  assert.equal(nudge("a", false, 1000), null);
});

test("the live preview places the design as the mockups do: proportions kept, top-aligned, centred", () => {
  const area = box(300, 200, 700, 680); // 400 x 480
  const d = designQuad(area, 1000, 500, 1.0); // wide: as wide as the area
  closeQuad(d, box(300, 200, 700, 400), 1e-6);
  const tall = designQuad(area, 500, 1000, 0.5); // tall, half size: 120 x 240, centred, at the top
  closeQuad(tall, box(440, 200, 560, 440), 1e-6);
  // The homography maps the corners it was made from.
  const h = homography([[0, 0], [1, 0], [1, 1], [0, 1]], [[10, 10], [110, 20], [100, 120], [5, 100]]);
  assert.deepEqual(apply(h, [1, 1]).map((v) => Math.round(v * 1000) / 1000), [100, 120]);
});
