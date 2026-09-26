// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  dayKey,
  formatTime,
  formatWhen,
  nextHour,
  scheduleLabel,
  spreadTimes,
  toWallClock,
  wallToInstant,
  zoneAbbreviation,
} from "./schedule.ts";

const CHI = "America/Chicago";

test("all at once when there is no daily limit or spacing", () => {
  assert.deepEqual(spreadTimes("2026-10-01T10:00", 3, null, 0), ["2026-10-01T10:00", "2026-10-01T10:00", "2026-10-01T10:00"]);
});

test("spacing puts each one after the last", () => {
  assert.deepEqual(spreadTimes("2026-10-01T10:00", 3, null, 30), ["2026-10-01T10:00", "2026-10-01T10:30", "2026-10-01T11:00"]);
});

test("a daily limit starts the next day again at the same time of day", () => {
  assert.deepEqual(spreadTimes("2026-10-01T10:00", 5, 2, 60), [
    "2026-10-01T10:00",
    "2026-10-01T11:00",
    "2026-10-02T10:00",
    "2026-10-02T11:00",
    "2026-10-03T10:00",
  ]);
});

test("across the autumn clock change the time of day stays 17:00", () => {
  // 1 Nov 2026 is when Chicago goes from CDT to CST.
  const t = spreadTimes("2026-10-31T17:00", 3, 1, 0);
  assert.deepEqual(t, ["2026-10-31T17:00", "2026-11-01T17:00", "2026-11-02T17:00"]);
  assert.deepEqual(
    t.map((w) => wallToInstant(w, CHI)!.toISOString()),
    ["2026-10-31T22:00:00.000Z", "2026-11-01T23:00:00.000Z", "2026-11-02T23:00:00.000Z"],
  );
});

test("a wall-clock time means that zone's time, whatever the computer's zone", () => {
  const at = wallToInstant("2026-09-28T17:00", CHI)!;
  assert.equal(at.toISOString(), "2026-09-28T22:00:00.000Z");
  assert.equal(toWallClock(at, CHI), "2026-09-28T17:00");
  assert.equal(toWallClock(at, "America/Los_Angeles"), "2026-09-28T15:00");
  assert.equal(wallToInstant("2026-03-08T02:30", CHI), null); // skipped by the spring change
  assert.equal(wallToInstant("nonsense", CHI), null);
});

test("every time is shown with its zone", () => {
  const at = new Date("2026-09-28T22:00:00Z");
  assert.equal(zoneAbbreviation(at, CHI), "CDT");
  assert.equal(zoneAbbreviation(new Date("2026-12-07T23:00:00Z"), "America/New_York"), "EST");
  assert.match(formatTime(at, CHI), /5:00\s?PM CDT$/);
  assert.match(formatWhen(at, CHI), /CDT$/);
  assert.equal(dayKey(new Date("2026-09-29T03:00:00Z"), CHI), "2026-09-28"); // still the 28th there
});

test("the first suggestion is the next whole hour on the zone's clock", () => {
  assert.equal(nextHour(CHI, new Date("2026-09-28T15:42:00Z")), "2026-09-28T11:00");
});

test("every status has words for the seller", () => {
  assert.equal(scheduleLabel("waiting"), "Waiting for the daily budget");
  assert.equal(scheduleLabel(undefined), "Scheduled");
});
