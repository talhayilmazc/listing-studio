// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { makeCover, moveBefore, nudge, sameOrder } from "./order.ts";

const IDS = ["a", "b", "c", "d"];

test("dropping an image on another puts it just before that one", () => {
  assert.deepEqual(moveBefore(IDS, "d", "b"), ["a", "d", "b", "c"]);
  assert.deepEqual(moveBefore(IDS, "a", "c"), ["b", "a", "c", "d"]);
  assert.deepEqual(moveBefore(IDS, "b", null), ["a", "c", "d", "b"]);
});

test("dropping an image on itself, or an unknown one, changes nothing", () => {
  assert.equal(moveBefore(IDS, "b", "b"), IDS);
  assert.equal(moveBefore(IDS, "x", "b"), IDS);
});

test("making an image the cover moves it first and keeps the rest in order", () => {
  assert.deepEqual(makeCover(IDS, "c"), ["c", "a", "b", "d"]);
  assert.deepEqual(makeCover(IDS, "a"), IDS);
});

test("nudging swaps with a neighbour and stops at the ends", () => {
  assert.deepEqual(nudge(IDS, "b", -1), ["b", "a", "c", "d"]);
  assert.deepEqual(nudge(IDS, "b", 1), ["a", "c", "b", "d"]);
  assert.equal(nudge(IDS, "a", -1), IDS);
  assert.equal(nudge(IDS, "d", 1), IDS);
});

test("sameOrder compares position by position", () => {
  assert.ok(sameOrder(IDS, [...IDS]));
  assert.ok(!sameOrder(IDS, makeCover(IDS, "b")));
});
