// "Copy this area to all garments" (seller feedback A3; lib/areaEditor.ts copyToFound,
// components/PrintAreaEditor.tsx): copies go only onto garments the analysis really found, by
// their own geometry; never where no garment was found; the editor says how many were found.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { centre, copyToFound, foundNote, inQuad, type Quad } from "./areaEditor.ts";

const box = (x: number, y: number, w: number, h: number): Quad => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
type A = { quad: Quad; suggested: Quad; tag: string };

// Six tees in two rows of three; the analysis found only the bottom three (their chest areas).
const found = [0, 1, 2].map((c) => box(100 + c * 500, 700, 140, 150));
const handAdded = [0, 1, 2].map((c) => box(100 + c * 500, 150, 140, 150));

test("a copy goes onto each found garment by its own geometry, never onto the hand-added areas", () => {
  // The seller's area on the first found garment: narrower and lower than the suggestion.
  const mine = box(115, 730, 110, 110);
  const areas: A[] = [
    { quad: mine, suggested: found[0], tag: "mine" },
    { quad: found[1], suggested: found[1], tag: "f1" },
    { quad: found[2], suggested: found[2], tag: "f2" },
    ...handAdded.map((q, i): A => ({ quad: q, suggested: q, tag: `top${i}` })),
  ];
  const done = copyToFound(areas, 0, found, (quad, garment) => ({ quad, suggested: garment, tag: "new" }));
  assert.equal(done.copied, 2);
  assert.equal(done.areas.length, 6); // no area made where no garment was found
  for (const i of [1, 2]) {
    // The same offset on its own garment: 15 px in and 30 px down, 110 px square.
    const q = done.areas[i].quad;
    assert.deepEqual(q.map(([x, y]) => [Math.round(x), Math.round(y)]), box(115 + i * 500, 730, 110, 110));
    assert.equal(done.areas[i].tag, `f${i}`); // its own subgroup and switches kept
  }
  for (const i of [3, 4, 5]) assert.deepEqual(done.areas[i].quad, handAdded[i - 3]); // left alone
  assert.equal(done.note, "3 of 6 garments found; add the others with Click a garment or Duplicate");
});

test("a found garment without an area gets one; the note follows the count", () => {
  const areas: A[] = [{ quad: found[0], suggested: found[0], tag: "mine" }];
  const done = copyToFound(areas, 0, found, (quad, garment) => ({ quad, suggested: garment, tag: "new" }));
  assert.equal(done.areas.length, 3);
  assert.deepEqual(done.areas.map((a) => a.tag), ["mine", "new", "new"]);
  assert.equal(done.note, null); // every garment was found
});

test("an area on a garment nobody found copies nowhere and says why", () => {
  const areas: A[] = [{ quad: handAdded[0], suggested: handAdded[0], tag: "top" }, { quad: found[0], suggested: found[0], tag: "f0" }];
  const done = copyToFound(areas, 0, found, (quad, garment) => ({ quad, suggested: garment, tag: "new" }));
  assert.equal(done.copied, 0);
  assert.equal(done.areas, areas);
  assert.match(done.note ?? "", /not on a garment the analysis found/);
  // Nothing found at all: nothing copied.
  assert.equal(copyToFound(areas, 1, [], (q, g) => ({ quad: q, suggested: g, tag: "x" })).copied, 0);
});

test("the geometry helpers", () => {
  assert.equal(inQuad(centre(found[0]), found[0]), true);
  assert.equal(inQuad([0, 0], found[0]), false);
  assert.equal(inQuad([250, 775], found[0], 1), false);
  assert.equal(inQuad([250, 775], found[0], 1.8), true);
  assert.equal(foundNote(3, 6), "3 of 6 garments found; add the others with Click a garment or Duplicate");
});

test("the editor copies with copyToFound and shows how many garments were found", () => {
  const editor = fs.readFileSync(path.join(import.meta.dirname, "..", "components", "PrintAreaEditor.tsx"), "utf8");
  assert.match(editor, /copyToFound\(areas, selected, found,/);
  assert.match(editor, /photo\.found_areas/);
  assert.match(editor, /data-testid="editor-found"/);
  assert.doesNotMatch(editor, /copyToGarment\(src\.quad, src\.suggested, a\.suggested\)/); // the old copy onto every area
});
