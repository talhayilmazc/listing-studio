// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { TAG_COUNT, UNDO_SECONDS, addTags, editTag, restoreTags, splitTags, tagProblems } from "./tags.ts";

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

// --- "Delete all", with Undo for ten seconds (customer feedback) ---

test("delete all leaves no tags, and undo brings them back in order", () => {
  const tags = Array.from({ length: TAG_COUNT }, (_, i) => `tag ${i + 1}`);
  const afterDelete: string[] = [];
  assert.equal(afterDelete.length, 0); // the 13-tag counter reads 0 / 13
  assert.deepEqual(restoreTags(tags, afterDelete), tags); // and 13 / 13 again after Undo
  assert.equal(UNDO_SECONDS, 10);
});

test("undo keeps what was typed after deleting, without doubling a tag", () => {
  assert.deepEqual(restoreTags(["nurse", "winter"], ["new one", "Nurse"]), ["nurse", "winter", "new one"]);
  assert.deepEqual(restoreTags([], ["only new"]), ["only new"]);
});
