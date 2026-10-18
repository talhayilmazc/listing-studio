// Part B in the browser: the design fills its print area exactly where the mockup puts it
// (lib/areaEditor.ts designQuad = pipeline/mockups.py place), "Fit area to design", the
// browser's trim matches the server's, designs can be removed from the upload, the "Use for
// listings" step's actions are real buttons, and the editor has the occlusion switch and overlay.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { areaSize, designQuad, fitAreaToDesign, type Quad } from "./areaEditor.ts";
import { TRIM_ALPHA, TRIM_EDGE, trimBox } from "./designTrim.ts";
import { SAMPLE_DESIGN } from "./mockups.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const CASES: { name: string; quad: Quad; design: [number, number]; size: number; corners: Quad }[] = JSON.parse(
  fs.readFileSync(path.join(FRONTEND, "..", "backend", "tests", "fixtures", "placement_cases.json"), "utf8"),
).cases;

test("the editor's preview puts the design exactly where the mockup does", () => {
  assert.ok(CASES.length >= 6);
  for (const c of CASES) {
    const q = designQuad(c.quad, c.design[0], c.design[1], c.size);
    q.forEach((p, i) => {
      assert.ok(Math.abs(p[0] - c.corners[i][0]) < 0.01 && Math.abs(p[1] - c.corners[i][1]) < 0.01,
        `${c.name}: corner ${i} ${p} vs ${c.corners[i]}`);
    });
  }
});

test("a design of the area's proportions fills it corner to corner; otherwise it touches the limiting side", () => {
  const area: Quad = [[200, 200], [600, 200], [600, 500], [200, 500]];
  assert.deepEqual(designQuad(area, 800, 600, 1), area);
  const wide = designQuad(area, 800, 400, 1);
  assert.deepEqual([wide[0], wide[1]], [[200, 200], [600, 200]]); // left to right, at the top
  assert.ok(Math.abs(wide[2][1] - 400) < 1e-9);
});

test("Fit area to design gives the area the design's proportions with its top-centre fixed", () => {
  const area: Quad = [[200, 200], [600, 200], [600, 500], [200, 500]]; // 400 x 300
  const fitted = fitAreaToDesign(area, 600, 600); // a square design: the area becomes 400 x 400
  assert.deepEqual([fitted[0], fitted[1]], [area[0], area[1]]);
  const s = areaSize(fitted);
  assert.ok(Math.abs(s.w - 400) < 1e-9 && Math.abs(s.h - 400) < 1e-9);
  assert.deepEqual(designQuad(fitted, 600, 600, 1), fitted); // now the design fills it exactly
  // Too tall for the photo: the height stays and the width follows, around the top-centre.
  const narrow = fitAreaToDesign(area, 300, 900, 1000, 700);
  const n = areaSize(narrow);
  assert.ok(Math.abs(n.h - 300) < 1e-9 && Math.abs(n.w - 100) < 1e-9);
  assert.ok(Math.abs((narrow[0][0] + narrow[1][0]) / 2 - 400) < 1e-9 && narrow[0][1] === 200);
  // A turned area keeps its angle.
  const turned: Quad = [[300, 180], [640, 240], [590, 520], [250, 460]];
  const t = fitAreaToDesign(turned, 700, 700);
  assert.deepEqual([t[0], t[1]], [turned[0], turned[1]]);
  const ts = areaSize(t);
  assert.ok(Math.abs(ts.w - ts.h) < 1e-6);
});

test("the browser trims a design's padding as the server does: faint edges kept, a 2 px edge added", () => {
  assert.equal(TRIM_ALPHA, 8);
  assert.equal(TRIM_EDGE, 2);
  const w = 100;
  const h = 80;
  const rgba = new Uint8ClampedArray(w * h * 4);
  const set = (x: number, y: number, a: number) => { rgba[(y * w + x) * 4 + 3] = a; };
  for (let y = 30; y < 50; y++) for (let x = 20; x < 60; x++) set(x, y, 255);
  for (let x = 20; x < 60; x++) set(x, 29, 20); // a faint row (alpha 20): a drawn pixel
  set(5, 5, 5); // alpha 5: padding noise, ignored
  assert.deepEqual(trimBox(rgba, w, h), [18, 27, 62, 52]);
  assert.equal(trimBox(new Uint8ClampedArray(16), 2, 2), null);
});

test("the editor's sample design has no padding of its own", () => {
  assert.deepEqual([SAMPLE_DESIGN.width, SAMPLE_DESIGN.height], [760, 800]);
  assert.match(decodeURIComponent(SAMPLE_DESIGN.url), /viewBox="120 80 760 800"/);
});

test("the editor: Fit area to design, the per-area occlusion switch and overlay, the trimmed preview", () => {
  const editor = read("components/PrintAreaEditor.tsx");
  for (const id of ["editor-fit-design", "editor-behind", "editor-show-front", "editor-occlusion-overlay", "editor-front-layer"]) {
    assert.ok(editor.includes(`data-testid="${id}"`), id);
  }
  assert.match(editor, /Design goes behind hair and arms/);
  assert.match(editor, /role="switch" aria-checked=\{sel\.occlusion\}/);
  assert.match(editor, /occlusion: a\.occlusion,/); // saved with the areas
  assert.match(editor, /behind: h\.present\.map\(\(a\) => a\.occlusion\)/); // the server's preview follows it
  assert.match(editor, /clipPath=\{`url\(#front-/); // the cutout only where the switch is on
  assert.match(editor, /trimmedDesign\(file\)/);
});

test("the upload: every design and every slot file has a visible Remove button that asks first", () => {
  const upload = read("components/MockupUpload.tsx");
  assert.match(upload, /data-testid="design-remove"/);
  assert.match(upload, /data-testid="mockup-slot-remove"/);
  assert.match(upload, /window\.confirm\(`Delete/);
  assert.match(upload, /deleteMockupDesignVersion/);
  assert.match(upload, /deleteMockupDesign\(file\.design\.id, true\)/);
  // A visible button (the shared button style, 44 px), never a hover-only control.
  assert.doesNotMatch(upload, /group-hover:|hover:opacity|opacity-0/);
});

test("the Use for listings step: Open batch and Download the ZIP are real buttons", () => {
  const upload = read("components/MockupUpload.tsx");
  assert.match(upload, /className="btn-primary min-h-11" onClick=\{onOpen\} data-testid="mockup-open-batch"/);
  assert.match(upload, /className="btn-primary min-h-11" href=\{api\.mockupZipUrl\(zip\.id\)\} download/);
  assert.doesNotMatch(upload, /tap text-brand-700 underline" onClick=\{onOpen\}/);
});
