// The same cases as backend/tests/test_size_chart_position.py: the strip shows what drafts get.
import assert from "node:assert/strict";
import { test } from "node:test";
import { END, arrange, defaultSlots, slotsFromOrder } from "./chartOrder.ts";

const photos = ["cover", "p2", "p3", "p4"];

test("the profile positions", () => {
  assert.deepEqual(arrange(photos, ["chart"], defaultSlots("after_cover", 1)), ["cover", "chart", "p2", "p3", "p4"]);
  assert.deepEqual(arrange(photos, ["chart"], defaultSlots("third", 1)), ["cover", "p2", "chart", "p3", "p4"]);
  assert.deepEqual(arrange(photos, ["chart"], defaultSlots("last", 1)), ["cover", "p2", "p3", "p4", "chart"]);
  assert.deepEqual(defaultSlots(null, 2), [END, END]);
  assert.deepEqual(arrange(photos, ["c1", "c2"], [1, 1]), ["cover", "c1", "c2", "p2", "p3", "p4"]);
});

test("a dragged order and its slots", () => {
  const order = ["cover", "p2", "chart", "p3"];
  assert.deepEqual(slotsFromOrder(order, ["chart"]), [2]);
  assert.deepEqual(arrange(["cover", "p2", "p3"], ["chart"], [2]), order);
  assert.deepEqual(slotsFromOrder(["cover", "p2", "chart"], ["chart"]), [END]);
  assert.deepEqual(slotsFromOrder(["chart", "cover", "p2"], ["chart"]), [1]);
  assert.deepEqual(arrange(["cover"], ["chart"], [2]), ["cover", "chart"]);
});
