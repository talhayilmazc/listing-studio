// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { matchesProfile } from "./profileSearch.ts";

const tee = { name: "Comfort Colors® Tees", content_template: "apparel" };

test("matches the name, the template and the reference title", () => {
  assert.ok(matchesProfile(tee, "comfort"));
  assert.ok(matchesProfile(tee, "APPAREL"));
  assert.ok(matchesProfile(tee, "nurse", "Funny Nurse Shirt, Flu Season Tee"));
  assert.ok(!matchesProfile(tee, "nurse"));
});

test("every word must match, in any order", () => {
  assert.ok(matchesProfile(tee, "tees comfort"));
  assert.ok(!matchesProfile(tee, "tees hoodie"));
  assert.ok(matchesProfile({ name: "Digital", content_template: "digital_products" }, "digital products"));
});

test("an empty search keeps everything, and accents do not matter", () => {
  assert.ok(matchesProfile(tee, "  "));
  assert.ok(matchesProfile({ name: "Hemşire Tişört", content_template: "apparel" }, "hemsire"));
});
