// Light and dark variants on the upload page and the template photo's colour chip.
import assert from "node:assert/strict";
import { test } from "node:test";
import { colourChip, fileLines, fillLabel, missingLine, variantMockups } from "./variants.ts";
import type { MockupPlan } from "./types.ts";

const photos: MockupPlan["photos"] = [
  { id: "w", name: "White", template: "Tees", colour: "light", colour_source: "auto" },
  { id: "s", name: "Sand", template: "Tees", colour: "light", colour_source: "auto" },
  { id: "b", name: "Black", template: "Tees", colour: "dark", colour_source: "auto" },
  { id: "n", name: "Navy", template: "Tees", colour: "dark", colour_source: "auto" },
];
const design = (over: Partial<MockupPlan["designs"][number]>): MockupPlan["designs"][number] => ({
  design_id: "d", variants: true, files: [], missing: [], missing_colours: [], lacking: [], uncertain: [], fill_other: false, ...over,
});

test("which file goes on which photos, before anything is made", () => {
  const both = design({
    files: [
      { file_id: "1", filename: "CDM290-white.png", variant: "light", photos: ["w", "s"] },
      { file_id: "2", filename: "CDM290-dark.png", variant: "dark", photos: ["b", "n"] },
    ],
  });
  assert.deepEqual(fileLines(both, photos), ["CDM290-white.png → 2 light-shirt photos", "CDM290-dark.png → 2 dark-shirt photos"]);
  assert.equal(missingLine(both, photos, "shirt"), null);
  assert.equal(fillLabel(both), null);
  assert.equal(variantMockups(both), 4);
});

test("one variant only: which photos get no mockup, why, and the one-click fix", () => {
  const one = design({
    files: [{ file_id: "1", filename: "BR100-white.png", variant: "light", photos: ["w", "s"] }],
    missing: ["b", "n"], missing_colours: ["dark"], lacking: ["dark"],
  });
  assert.equal(missingLine(one, photos, "shirt"),
    "There is no dark (-dark) version of this design, so 2 dark-shirt photos get no mockup of it: Black, Navy.");
  // The suffix names the ink: a dark shirt needs the light-ink (-white) file.
  assert.match(missingLine(one, photos, "ink")!, /no light \(-white\) version/);
  assert.equal(fillLabel(one), "Use BR100-white.png on dark shirts too");
  assert.equal(fillLabel({ ...one, fill_other: true }), null);
});

test("the photo's chip asks only when the colour is not certain", () => {
  assert.deepEqual(colourChip({ colour: "light", colour_source: "auto" }), { text: "Light shirt", ask: false });
  assert.deepEqual(colourChip({ colour: "dark", colour_source: "seller" }), { text: "Dark shirt", ask: false });
  assert.deepEqual(colourChip({ colour: "light", colour_source: "uncertain" }), { text: "Light shirt? Check", ask: true });
  assert.deepEqual(colourChip({ colour: null, colour_source: null }), { text: "Light or dark?", ask: true });
});
