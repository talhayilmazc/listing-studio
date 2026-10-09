// The upload page's preview must place every file exactly where the server will:
// both suites read backend/tests/fixtures/grouping_cases.json.
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { autoMode, place, preview, previewText, skuOf, type GroupingMode } from "./grouping.ts";

const cases = JSON.parse(fs.readFileSync(new URL("../../backend/tests/fixtures/grouping_cases.json", import.meta.url), "utf8"));

test("every shared file name reads the same SKU as on the server", () => {
  for (const [name, expected] of cases.sku_of) assert.equal(skuOf(name), expected, name);
});

test("every shared case lands in the same group as on the server", () => {
  for (const [folder, name, mode, key, sku] of cases.place) {
    assert.deepEqual(place(folder, name, mode as GroupingMode | null), [key, sku], `${folder}/${name} (${mode})`);
  }
});

const flat = ["BR5229-1.png", "br5229_2.jpg", "BR5229 (3).jpg", "BR5229 copy.png", "AB1234.png", "IMG_4411.jpg"].map((name) => ({ folder: "", name }));

test("the preview counts listings and unsorted photos for each choice", () => {
  assert.deepEqual(preview(flat, "sku"), { listings: 2, unsorted: 1 });
  assert.deepEqual(preview(flat, "one"), { listings: 1, unsorted: 0 });
  assert.deepEqual(preview(flat, "folder"), { listings: 1, unsorted: 0 });
  assert.equal(previewText({ listings: 42, unsorted: 3 }), "42 listings, 3 photos unsorted");
  assert.equal(previewText({ listings: 1, unsorted: 0 }), "1 listing");
});

test("the default follows what was dropped, and the seller's last choice for that kind wins", () => {
  assert.equal(autoMode([{ folder: "BR6001", name: "a.png" }, { folder: "", name: "x.png" }]), "folder");
  assert.equal(autoMode(flat), "sku");
  assert.equal(autoMode([{ folder: "", name: "front.png" }, { folder: "", name: "IMG_1.jpg" }]), "one");
  assert.equal(autoMode(flat, { flat: "one" }), "one");
  assert.equal(autoMode(flat, { folders: "sku" }), "sku"); // a choice for folders does not apply to a flat drop
});
