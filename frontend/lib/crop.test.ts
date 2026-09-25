// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_ZOOM, centredCrop, clampView, cropOf, cropStyle, pan, viewOf, zoomAround } from "./crop.ts";

// A portrait 1600 x 2000 mockup in a 320px frame.
const W = 1600, H = 2000, F = 320;
const near = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);

test("with no crop the frame shows the centred square of the short side", () => {
  const c = cropOf(viewOf(null, W, H, F), W, H, F);
  near(c.x, 0); near(c.y, 200); near(c.size, 1600);
  assert.deepEqual(centredCrop(W, H), { x: 0, y: 200, size: 1600 });
});

test("a saved crop comes back exactly", () => {
  const crop = { x: 300, y: 450, size: 800 };
  const c = cropOf(viewOf(crop, W, H, F), W, H, F);
  near(c.x, 300); near(c.y, 450); near(c.size, 800);
});

test("the image always covers the frame: panning stops at the edges", () => {
  const v = pan(viewOf(null, W, H, F), 1000, 1000, W, H, F); // dragged far right and down
  const c = cropOf(v, W, H, F);
  near(c.x, 0); near(c.y, 0);
  const far = cropOf(pan(v, -5000, -5000, W, H, F), W, H, F);
  near(far.x + far.size, W); near(far.y + far.size, H);
});

test("zoom stays between 1 and the maximum", () => {
  const v = viewOf(null, W, H, F);
  assert.equal(clampView({ ...v, zoom: 0.2 }, W, H, F).zoom, 1);
  assert.equal(zoomAround(v, 50, F / 2, F / 2, W, H, F).zoom, MAX_ZOOM);
});

test("zooming keeps the point under the cursor in place", () => {
  const v = viewOf(null, W, H, F);
  const z = zoomAround(v, 2, 160, 160, W, H, F);
  const before = cropOf(v, W, H, F), after = cropOf(z, W, H, F);
  // The frame centre is the same image point before and after.
  near(before.x + before.size / 2, after.x + after.size / 2);
  near(before.y + before.size / 2, after.y + after.size / 2);
  near(after.size, 800);
});

test("a thumbnail can draw the same crop at any size", () => {
  const s = cropStyle({ x: 400, y: 200, size: 800 }, W, H, 96);
  near(s.width, 192); near(s.height, 240); near(s.left, -48); near(s.top, -24);
});
