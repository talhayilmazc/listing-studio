// The design editor (components/DesignEditor.tsx, Part B): what it offers and how it uses the
// shared rules. The arithmetic itself is tested against the server in lib/designEdits.test.ts.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const FRONTEND = path.join(import.meta.dirname, "..");
const editor = fs.readFileSync(path.join(FRONTEND, "components", "DesignEditor.tsx"), "utf8");
const upload = fs.readFileSync(path.join(FRONTEND, "components", "MockupUpload.tsx"), "utf8");

test("move, scale with proportions kept by default (Shift switches), turn, free corners, reset", () => {
  assert.match(editor, /const \[keepRatio, setKeepRatio\] = useState\(true\)/);
  assert.match(editor, /const proportional = keepRatio !== e\.shiftKey;/);
  assert.match(editor, /e\.shiftKey \? STEP\.big : STEP\.small/); // arrow keys, Shift for bigger steps
  for (const id of ["design-editor-ratio", "design-editor-free", "design-editor-reset", "design-editor-rotate", "design-editor-corner", "design-editor-box"]) {
    assert.ok(editor.includes(`data-testid="${id}"`), id);
  }
  assert.match(editor, /setAdjust\(\{ \.\.\.NEUTRAL \}\)/); // Reset: the automatic fit
});

test("the preview is drawn with the renderer's own rules (points, look, recolouring)", () => {
  assert.match(editor, /photoPoints\(areaPx, tw, th, photo\.size, adjust\)/);
  // Recolouring, the trim and the look: the one pipeline the result tiles' preview draws too.
  assert.match(editor, /printPixels\(src, trim, previewRegions, adjust\)/);
  assert.match(editor, /matrix3d\(tw, th, screen\)/);
});

test("look sliders, recolour tools, apply scopes with undo, and saving re-renders the mockup", () => {
  for (const id of ["design-editor-tolerance", "design-editor-recolour-for", "design-editor-undo", "design-editor-save"]) {
    assert.ok(editor.includes(`data-testid="${id}"`), id);
  }
  // One per slider, tool and scope (their ids are built from the lists).
  assert.match(editor, /data-testid=\{`design-editor-\$\{s\.key\}`\}/);
  assert.match(editor, /data-testid=\{`design-editor-tool-\$\{t\}`\}/);
  assert.match(editor, /data-testid=\{`design-editor-apply-\$\{s\}`\}/);
  assert.match(editor, /\(\["move", "rect", "lasso", "wand"\] as Tool\[\]\)/);
  assert.match(editor, /api\.applyAdjustment\(/);
  assert.match(editor, /api\.restoreAdjustments\(lastApply\.adjust\)/);
  assert.match(editor, /await remake\(\[\[designId, photoId\]\]\)/);
  assert.match(editor, /onRun\(await api\.rerenderMockups\(runId, pairs\)\)/);
  assert.match(editor, /Your file is never changed/);
});

test("every mockup in the results opens the editor", () => {
  const tile = fs.readFileSync(path.join(FRONTEND, "components", "MockupTile.tsx"), "utf8");
  assert.match(tile, /data-testid="mockup-adjust"/);
  assert.match(upload, /<MockupTile /);
  assert.match(upload, /api\.mockupEdits\(run\.id\)/);
  // A re-made mockup's image is never a stale copy: its URL carries its version.
  assert.match(tile, /api\.mockupOutputImageUrl\(runId, output\.id, 224, output\.stamp \?\? ""\)/);
});
