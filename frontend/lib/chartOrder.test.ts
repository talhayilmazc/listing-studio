// The same cases as backend/tests/test_size_chart_position.py: the strip shows what drafts get.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  END, FEWER_PHOTOS_RULE, MAX_IMAGES, NOT_ON_ETSY_NOTE, arrange, defaultSlots, editorStrip, moveChartTo, nudgeChart, ordinal,
  placeLabel, placementFromOrder, placementFromStrip, presetPlacement, samePlacement, slotsFromOrder,
} from "./chartOrder.ts";

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

// --- each chart in its own place, set on the profile (the same cases as the backend's) ---

const A = 901, B = 902;

test("the profile editor shows twenty places: the cover, mockups, and each chart where it sits", () => {
  const strip = editorStrip(presetPlacement("after_cover", [A, B]));
  assert.equal(strip.length, MAX_IMAGES);
  assert.deepEqual(strip.slice(0, 4), ["cover", "chart:901", "chart:902", "photo:2"]);
  assert.deepEqual(editorStrip(presetPlacement("third", [A])).slice(0, 4), ["cover", "photo:2", "chart:901", "photo:3"]);
  const last = editorStrip(presetPlacement("last", [A, B]));
  assert.deepEqual(last.slice(-2), ["chart:901", "chart:902"]);
  assert.equal(last.filter((t) => t.startsWith("photo:")).length, 17);
  assert.equal(placeLabel(last, "chart:902"), "last");
  assert.equal(placeLabel(strip, "chart:902"), "3rd");
});

test("dragging writes the exact positions, each chart its own, in the order they appear", () => {
  let strip = editorStrip(presetPlacement("last", [A, B]));
  strip = moveChartTo(strip, "chart:902", 1); // the care card second
  strip = moveChartTo(strip, "chart:901", 3); // the size chart fourth
  assert.deepEqual(strip.slice(0, 5), ["cover", "chart:902", "photo:2", "chart:901", "photo:3"]);
  const saved = placementFromStrip(strip);
  assert.deepEqual(saved, [{ listing_image_id: B, slot: 1 }, { listing_image_id: A, slot: 2 }]);
  // What is saved draws the same row again, and puts a real listing in that order.
  assert.deepEqual(editorStrip(saved), strip);
  assert.deepEqual(
    arrange(["cover", "back", "detail"], saved.map((p) => p.listing_image_id), saved.map((p) => p.slot)),
    ["cover", B, "back", A, "detail"],
  );
  // Two charts in the same gap keep the order they were dragged into.
  const together = placementFromStrip(moveChartTo(strip, "chart:901", 1));
  assert.deepEqual(together, [{ listing_image_id: A, slot: 1 }, { listing_image_id: B, slot: 1 }]);
  assert.ok(!samePlacement(saved, together) && samePlacement(saved, placementFromStrip(strip)));
});

test("a chart can go anywhere but the cover's place", () => {
  const strip = editorStrip(presetPlacement("third", [A]));
  assert.equal(moveChartTo(strip, "chart:901", 0)[0], "cover");
  assert.equal(moveChartTo(strip, "chart:901", 0)[1], "chart:901");
  assert.equal(nudgeChart(moveChartTo(strip, "chart:901", 1), "chart:901", -1)[1], "chart:901"); // already second: stays
  assert.equal(nudgeChart(strip, "chart:901", 1).indexOf("chart:901"), 3);
  assert.equal(nudgeChart(strip, "chart:901", -1).indexOf("chart:901"), 1);
  const end = moveChartTo(strip, "chart:901", 99);
  assert.equal(end.indexOf("chart:901"), MAX_IMAGES - 1);
  assert.deepEqual(placementFromStrip(end), [{ listing_image_id: A, slot: END }]); // the end is "last", however many photos
  assert.deepEqual(nudgeChart(end, "chart:901", 1), end);
  assert.deepEqual(moveChartTo(strip, "photo:2", 5), strip); // only charts move
});

test("a listing with fewer photos than the place chosen gets the chart last, and the screen says so", () => {
  assert.deepEqual(arrange(["cover", "p2"], [A], [5]), ["cover", "p2", A]);
  assert.deepEqual(arrange(["cover", "p2", "p3"], [A, B], [1, 7]), ["cover", A, "p2", "p3", B]);
  assert.match(FEWER_PHOTOS_RULE, /fewer photos.*goes last/);
  assert.match(NOT_ON_ETSY_NOTE, /never changes drafts already on Etsy.*new drafts.*Replace images/);
});

test("a group's own strip saves the charts in the order they are dragged", () => {
  const keys = ["chart:901", "chart:902"];
  assert.deepEqual(placementFromOrder(["cover", "chart:902", "p2", "chart:901"], keys), [
    { listing_image_id: B, slot: 1 }, { listing_image_id: A, slot: END },
  ]);
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 20].map(ordinal), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "20th"]);
});
