// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { addTags, editTag, splitTags, tagProblems } from "./tags.ts";

test("commas, semicolons and new lines separate tags", () => {
  assert.deepEqual(splitTags("nurse, winter, sweatshirt"), ["nurse", "winter", "sweatshirt"]);
  assert.deepEqual(splitTags("nurse;winter\nsweatshirt ,, "), ["nurse", "winter", "sweatshirt"]);
  assert.deepEqual(splitTags("  funny   nurse  shirt "), ["funny nurse shirt"]);
});

test("a pasted list is added as separate tags, without duplicates", () => {
  assert.deepEqual(addTags(["nurse"], "Nurse, winter, sweatshirt, winter"), ["nurse", "winter", "sweatshirt"]);
});

test("typing a comma inside a tag splits it in place", () => {
  assert.deepEqual(editTag(["a", "nurse", "c"], 1, "nurse, winter"), ["a", "nurse", "winter", "c"]);
  assert.deepEqual(editTag(["a", "b"], 0, "abc"), ["abc", "b"]);
  assert.deepEqual(editTag(["winter", "b"], 1, "b, winter"), ["winter", "b"]);
});

test("each tag is checked: length and duplicates", () => {
  assert.deepEqual(tagProblems(["nurse", "christmas tee for her", "Nurse", "ok"]), [
    "duplicate",
    "over 20 characters",
    "duplicate",
    null,
  ]);
});
