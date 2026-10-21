// Etsy's own rules for a draft's title and tags: the same cases as the backend
// (backend/tests/fixtures/etsy_text_cases.json, tests/test_hand_written.py).
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { draftErrors, tagErrors, titleErrors } from "./etsyText.ts";

const cases = JSON.parse(fs.readFileSync(new URL("../../backend/tests/fixtures/etsy_text_cases.json", import.meta.url), "utf8"));

test("titles: Etsy's characters, %, :, & and + once each, 140 at most", () => {
  for (const c of cases.titles) assert.equal(titleErrors(c.text).length === 0, c.ok, JSON.stringify(c.text.slice(0, 40)));
});

test("tags: Etsy's characters, 20 at most, no comma", () => {
  for (const c of cases.tags) assert.equal(tagErrors([c.text]).length === 0, c.ok, JSON.stringify(c.text));
  assert.deepEqual(tagErrors(Array(14).fill("t")), ["More than 13 tags (14); Etsy takes 13 at most"]);
});

test("a title and five tags are fine for Etsy; 13 tags is the app's note, not Etsy's rule", () => {
  assert.deepEqual(draftErrors("Frog Reading Club Shirt", ["frog", "book", "pond", "reader", "cottagecore"], "Soft."), []);
  assert.deepEqual(draftErrors("", [], "Body"), ["Title is empty"]);
});
