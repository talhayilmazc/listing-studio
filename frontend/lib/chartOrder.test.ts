// The same cases as backend/tests/test_size_chart_position.py: the strip shows what drafts get.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  END, FEWER_PHOTOS_RULE, MAX_IMAGES, NOT_ON_ETSY_NOTE, arrange, chartsCrowdNotice, defaultSlots, editorStrip, fit, imageLimit,
  leftOutNotice, limitNotice, moveChartTo, nudgeChart, ordinal, placeLabel, placementFromOrder, placementFromStrip, presetPlacement,
  samePlacement, slotsFromOrder,
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

// --- Etsy's 20-image limit (the same cases as backend/tests/test_image_limit.py) ---

const names = (n: number) => ["cover.jpg", ...Array.from({ length: n - 1 }, (_, i) => `p${String(i + 2).padStart(2, "0")}.jpg`)];
const three = ["c1", "c2", "c3"];

test("exactly twenty images all fit", () => {
  const [kept, over] = fit(names(17), 3);
  assert.equal(kept.length, 17);
  assert.deepEqual(over, []);
  assert.equal(arrange(kept, three, [1, 3, END]).length, MAX_IMAGES);
  assert.deepEqual(fit(names(20), 0)[1], []);
  assert.equal(imageLimit(names(17).map((n) => ({ status: "processed", original_filename: n })), 3), null);
});

test("the twenty-first image is the last photo, and it is left out", () => {
  assert.deepEqual(fit(names(18), 3)[1], ["p18.jpg"]);
  assert.deepEqual(fit(names(21), 0)[1], ["p21.jpg"]);
  const over = imageLimit(names(18).map((n) => ({ status: "processed", original_filename: n })), 3);
  assert.deepEqual(over, { total: 21, limit: 20, left_out: ["p18.jpg"] });
  assert.equal(
    limitNotice(over!),
    "21 images: Etsy allows 20. The last photo will be left out: p18.jpg. Reorder or remove photos to choose.",
  );
  assert.equal(
    limitNotice({ total: 22, limit: 20, left_out: ["a.jpg", "b.jpg"] }),
    "22 images: Etsy allows 20. The last 2 photos will be left out: a.jpg, b.jpg. Reorder or remove photos to choose.",
  );
  // A photo that failed to process is never sent, so it does not count.
  const withFailed = [...names(17).map((n) => ({ status: "processed", original_filename: n })), { status: "failed", original_filename: "bad.jpg" }];
  assert.equal(imageLimit(withFailed, 3), null);
});

test("three charts keep their places when photos are left out", () => {
  for (const photos of [17, 18, 30]) {
    const order = arrange(fit(names(photos), 3)[0], three, [1, 3, END]);
    assert.equal(order.length, 20);
    assert.deepEqual(three.map((c) => order.indexOf(c) + 1), [2, 5, 20]);
    assert.equal(order[0], "cover.jpg");
  }
  assert.deepEqual(fit(names(4), 19), [["cover.jpg"], ["p02.jpg", "p03.jpg", "p04.jpg"]]);
});

test("what a job says it left out", () => {
  assert.equal(leftOutNotice([]), "");
  assert.equal(leftOutNotice(undefined), "");
  assert.equal(leftOutNotice(["z.jpg"]), "Etsy allows 20 images, so the last photo was left out: z.jpg.");
  assert.equal(leftOutNotice(["y.jpg", "z.jpg"]), "Etsy allows 20 images, so the last 2 photos were left out: y.jpg, z.jpg.");
});

test("the profile is told when its charts alone leave fewer than two places for photos", () => {
  assert.equal(chartsCrowdNotice(3), null);
  assert.equal(chartsCrowdNotice(18), null); // the cover and one more photo still fit
  assert.match(chartsCrowdNotice(19) ?? "", /^19 size charts: Etsy allows 20 images, so only the cover fits/);
  // The row of places never shows more than Etsy keeps.
  assert.equal(editorStrip(presetPlacement("last", Array.from({ length: 19 }, (_, i) => 900 + i))).length, 20);
  assert.equal(editorStrip(presetPlacement("last", [A, B])).length, 20);
});
