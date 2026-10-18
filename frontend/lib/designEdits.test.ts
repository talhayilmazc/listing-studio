// A design adjusted on a mockup, its look and recoloured parts (lib/designEdits.ts): the editor
// gives the same numbers as the server (backend pipeline/design_edits.py), read from the shared
// backend/tests/fixtures/design_edit_cases.json, so the preview and the rendered mockup match.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import type { Quad } from "./areaEditor.ts";
import {
  APPLY_SCOPES,
  RANGES,
  type Adjust,
  type Region,
  clean,
  compose,
  delta,
  lookPixels,
  photoPoints,
  recolourPixels,
  regionMask,
  unitPoints,
} from "./designEdits.ts";
import { designQuad } from "./areaEditor.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const cases = JSON.parse(fs.readFileSync(path.join(FRONTEND, "..", "backend", "tests", "fixtures", "design_edit_cases.json"), "utf8"));
const close = (a: number, b: number, eps = 1e-3) => assert.ok(Math.abs(a - b) <= eps, `${a} vs ${b}`);

test("the design's corners match the server for every adjustment (moved, scaled, stretched, turned, free corners)", () => {
  assert.ok(cases.points.length >= 7);
  for (const c of cases.points) {
    const unit = unitPoints(c.quad as Quad, c.design[0], c.design[1], c.size, c.adjust);
    unit.forEach((p, i) => { close(p[0], c.unit[i][0], 1e-5); close(p[1], c.unit[i][1], 1e-5); });
    const photo = photoPoints(c.quad as Quad, c.design[0], c.design[1], c.size, c.adjust);
    photo.forEach((p, i) => { close(p[0], c.photo[i][0], 1e-2); close(p[1], c.photo[i][1], 1e-2); });
  }
});

test("no adjustment is the automatic fit the editor already draws", () => {
  const c = cases.points[0];
  assert.equal(c.adjust, null);
  const fit = designQuad(c.quad as Quad, c.design[0], c.design[1], c.size);
  photoPoints(c.quad as Quad, c.design[0], c.design[1], c.size, null).forEach((p, i) => { close(p[0], fit[i][0]); close(p[1], fit[i][1]); });
});

test("proportional scale keeps the aspect; a stretch changes one side only", () => {
  const q: Quad = [[0, 0], [400, 0], [400, 300], [0, 300]];
  const w = (pts: Quad) => Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]);
  const h = (pts: Quad) => Math.hypot(pts[3][0] - pts[0][0], pts[3][1] - pts[0][1]);
  const base = photoPoints(q, 300, 300, 1, null);
  const scaled = photoPoints(q, 300, 300, 1, { scale: 0.5 });
  close(w(scaled) / h(scaled), w(base) / h(base));
  close(w(scaled), w(base) * 0.5);
  const wide = photoPoints(q, 300, 300, 1, { sx: 1.5 });
  close(w(wide), w(base) * 1.5);
  close(h(wide), h(base));
});

test("the look matches the server, pixel for pixel", () => {
  for (const c of cases.looks) {
    const px = Uint8ClampedArray.from(c.pixels);
    lookPixels(px, c.adjust);
    assert.deepEqual(Array.from(px), c.out, c.name);
  }
});

test("the look's ranges are small and clamped", () => {
  assert.deepEqual(RANGES.opacity, [0.3, 1]);
  assert.deepEqual(RANGES.brightness, [-0.25, 0.25]);
  assert.deepEqual(RANGES.contrast, [-0.25, 0.25]);
  assert.deepEqual(clean({ opacity: 0.05, brightness: 2, contrast: -9 }), { ...clean({ opacity: 0.3 })!, brightness: 0.25, contrast: -0.25 });
  assert.equal(clean({ dx: 0, scale: 1, opacity: 1 }), null); // neutral: nothing to store ("Reset")
});

test("selections match the server: rectangle, lasso, wand (tolerance), nothing on padding", () => {
  for (const c of cases.regions) {
    const px = Uint8ClampedArray.from(c.pixels);
    const mask = regionMask(px, c.width, c.height, c.region as Region);
    assert.deepEqual(Array.from(mask.keys()).filter((i) => mask[i]), c.mask, c.name);
  }
});

test("recolouring replaces the colour and keeps every alpha (soft edges stay soft)", () => {
  for (const c of cases.regions) {
    const px = Uint8ClampedArray.from(c.pixels);
    recolourPixels(px, c.width, c.height, [c.region as Region]);
    assert.deepEqual(Array.from(px), c.recoloured, c.name);
    for (let i = 3; i < px.length; i += 4) assert.equal(px[i], c.pixels[i]); // alpha untouched
  }
});

test("Apply this change to…: the change composes on top of each one's own adjustment", () => {
  for (const c of cases.compose) {
    const got = compose(c.target, delta(c.before, c.after));
    if (c.result === null) assert.equal(got, null, c.name);
    else for (const k of Object.keys(c.result) as (keyof Adjust)[]) {
      if (k === "corners") assert.deepEqual(got![k], c.result[k], c.name);
      else close(got![k] as number, c.result[k], 1e-5);
    }
  }
  assert.deepEqual(Object.keys(APPLY_SCOPES), ["design_photos", "photo_designs", "template_all"]);
});
