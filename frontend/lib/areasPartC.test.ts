// Areas, subgroups and templates (seller feedback Part C): "Duplicate area", proportional resize
// with edge handles, "Print on all garments", the area's subgroup chooser with "Any" and coloured
// labels, account presets and the file-name prefix (lib/areaEditor.ts, lib/presets.ts,
// components/PrintAreaEditor.tsx, components/SubgroupPresets.tsx).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { duplicateQuad, edgeHandles, resizeEdge, resizeKeep, subgroupColour, SUBGROUP_COLOURS, type Quad } from "./areaEditor.ts";
import { cleanPrefix, prefixedExample } from "./presets.ts";
import { ANY_SUBGROUP, areaSubgroup } from "./mockups.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (f: string) => fs.readFileSync(path.join(FRONTEND, f), "utf8");
const box = (x: number, y: number, w: number, h: number): Quad => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
const size = (q: Quad) => ({ w: Math.hypot(q[1][0] - q[0][0], q[1][1] - q[0][1]), h: Math.hypot(q[3][0] - q[0][0], q[3][1] - q[0][1]) });

test("Duplicate area: a small offset, the same size and angle; it stays inside the photo", () => {
  const q = box(100, 100, 200, 300);
  const d = duplicateQuad(q, 1000, 1000);
  assert.deepEqual(d.map(([x, y]) => [x - 30, y - 30]), q);
  const edge = box(850, 850, 140, 140); // near the bottom right: it goes up and left instead
  assert.deepEqual(duplicateQuad(edge, 1000, 1000)[0], [820, 820]);
  assert.deepEqual(size(d), size(q));
});

test("corner handles keep the proportions; edge handles move one side", () => {
  const q = box(100, 100, 200, 100);
  const bigger = resizeKeep(q, 2, [400, 260]); // the bottom right corner dragged out
  const s = size(bigger);
  assert.ok(Math.abs(s.w / s.h - 2) < 1e-9); // still 2:1
  assert.deepEqual(bigger[0], [100, 100]); // the opposite corner stays
  assert.ok(s.w > 200);
  const wider = resizeEdge(q, 1, [380, 150]); // the right side dragged
  assert.deepEqual(wider, box(100, 100, 280, 100));
  const lower = resizeEdge(q, 2, [150, 260]); // the bottom side dragged
  assert.deepEqual(lower, box(100, 100, 200, 160));
  assert.equal(edgeHandles(q).length, 4);
  assert.ok(size(resizeKeep(q, 2, [101, 101])).h >= 12); // never smaller than the minimum
});

test("Any prints the design's own file; each subgroup has its own label colour", () => {
  const groups = [{ id: "w", name: "White", use_on: "light", suffix: "-white", position: 0 }, { id: "b", name: "Black", use_on: "dark", suffix: "-black", position: 1 }];
  assert.equal(areaSubgroup({ subgroup_id: null, tone: "light", subgroup_any: true }, "w", groups), ANY_SUBGROUP);
  assert.equal(areaSubgroup({ subgroup_id: "b", tone: "light" }, "w", groups)?.name, "Black");
  assert.notEqual(subgroupColour(0), subgroupColour(1));
  assert.equal(subgroupColour(SUBGROUP_COLOURS.length), subgroupColour(0)); // any number of subgroups
});

test("the file-name prefix is cleaned the way the server cleans it", () => {
  assert.equal(cleanPrefix(" CC "), "CC");
  assert.equal(cleanPrefix("Comfort Colors"), "Comfort-Colors");
  assert.equal(cleanPrefix('a/b\\c:d*e?f"g<h>i|j'), "abcdefghij");
  assert.equal(cleanPrefix("ŞİRKET"), "RKET");
  assert.equal(cleanPrefix("   "), null);
  assert.equal(cleanPrefix("x".repeat(50))?.length, 20);
  assert.equal(prefixedExample("CC"), "CC-BR100-01.jpg");
});

test("the editor offers Duplicate, Keep proportions, edge handles, Print on all garments and the chooser", () => {
  const editor = read("components/PrintAreaEditor.tsx");
  for (const id of ["editor-duplicate", "editor-ratio", "editor-edge", "editor-print-all", "editor-subgroup-chooser", "editor-subgroup-option"]) {
    assert.ok(editor.includes(`data-testid="${id}"`), id);
  }
  assert.match(editor, /const \[keepRatio, setKeepRatio\] = useState\(true\)/);
  assert.match(editor, /keepRatio !== e\.shiftKey \? resizeKeep/);
  assert.match(editor, /\[\.\.\.subgroups, ANY_SUBGROUP\]/);
  assert.match(editor, /subgroup_any: !!a\.subgroupAny/);
  assert.match(editor, /api\.saveMockupAreas\(photo\.id, payload\(\), true, size, printAll\)/);
});

test("presets: manage them, assign them to a template, save a subgroup as one; set a prefix", () => {
  const presets = read("components/SubgroupPresets.tsx");
  for (const id of ["subgroup-presets", "preset-add", "assign-presets", "assign-presets-go", "subgroup-save-preset", "template-file-prefix-input"]) {
    assert.ok(presets.includes(`data-testid="${id}"`), id);
  }
  const page = read("app/(app)/templates/page.tsx");
  assert.match(page, /<PresetsManager presets=\{presets\}/);
  assert.match(page, /<AssignPresets template=\{template\}/);
  assert.match(page, /<FilePrefixField template=\{template\}/);
  assert.match(page, /data-testid="template-use-presets"/);
});
