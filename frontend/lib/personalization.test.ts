// Run with: npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_QUESTION, personalizationErrors } from "./personalization.ts";

const base = { enabled: true, question_text: "Name", instructions: "", required: false, max_allowed_characters: null };

test("an off setting is always valid; an on one is checked against the limits", () => {
  assert.deepEqual(personalizationErrors({ ...base, enabled: false, question_text: "x".repeat(99) }), []);
  assert.deepEqual(personalizationErrors(base), []);
  assert.equal(personalizationErrors({ ...base, question_text: "x".repeat(MAX_QUESTION + 1) }).length, 1);
  assert.equal(personalizationErrors({ ...base, max_allowed_characters: 0 }).length, 1);
  assert.equal(personalizationErrors({ ...base, max_allowed_characters: 1025 }).length, 1);
  assert.equal(personalizationErrors({ ...base, instructions: "x".repeat(257) }).length, 1);
});
