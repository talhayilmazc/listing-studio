// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_REPLACE_MODE, replaceModes, replacedNotice } from "./replaceModes.ts";

const [photos, full] = replaceModes("this group's, in the order shown");

test("Replace images opens on photos only, which keeps the text and is not a listing generated", () => {
  assert.equal(DEFAULT_REPLACE_MODE, "photos");
  assert.equal(photos.mode, "photos");
  assert.equal(photos.label, "Photos only (keep title and tags)");
  assert.equal(photos.changes.length, 1);
  assert.match(photos.keeps.join(" "), /Title, tags and description/);
  assert.match(photos.counts, /Does not count as a listing generated/);
});

test("the full option says it rewrites the title and tags and counts one", () => {
  assert.equal(full.mode, "full");
  assert.match(full.changes.join(" "), /Title and all 13 tags/);
  assert.match(full.counts, /Counts as 1 listing generated/);
});

test("both options name where the photos come from, and the notice says what was done", () => {
  for (const o of [photos, full]) assert.ok(o.changes[0].includes("this group's, in the order shown"));
  assert.match(replacedNotice("photos", "NW1042"), /title, tags and description were not changed/);
  assert.match(replacedNotice("full", "NW1042"), /Photos, title and tags replaced/);
});
