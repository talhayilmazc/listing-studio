// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { dayKey, fromLocalInput, nextHour, scheduleLabel, spreadTimes, toLocalInput } from "./schedule.ts";

const start = new Date(2026, 9, 1, 10, 0); // 1 Oct 2026, 10:00 local

test("all at once when there is no daily limit or spacing", () => {
  const t = spreadTimes(start, 3, null, 0);
  assert.deepEqual(t.map(toLocalInput), ["2026-10-01T10:00", "2026-10-01T10:00", "2026-10-01T10:00"]);
});

test("spacing puts each one after the last", () => {
  const t = spreadTimes(start, 3, null, 30);
  assert.deepEqual(t.map(toLocalInput), ["2026-10-01T10:00", "2026-10-01T10:30", "2026-10-01T11:00"]);
});

test("a daily limit starts the next day again at the same time of day", () => {
  const t = spreadTimes(start, 5, 2, 60);
  assert.deepEqual(t.map(toLocalInput), [
    "2026-10-01T10:00",
    "2026-10-01T11:00",
    "2026-10-02T10:00",
    "2026-10-02T11:00",
    "2026-10-03T10:00",
  ]);
});

test("days are local calendar days, across a month end", () => {
  const t = spreadTimes(new Date(2026, 9, 31, 9, 0), 2, 1, 0);
  assert.deepEqual(t.map(dayKey), ["2026-10-31", "2026-11-01"]);
});

test("datetime-local values round-trip in local time", () => {
  assert.equal(toLocalInput(fromLocalInput("2026-10-01T10:05")!), "2026-10-01T10:05");
  assert.equal(fromLocalInput(""), null);
  assert.equal(fromLocalInput("nonsense"), null);
});

test("the first suggestion is the next whole hour", () => {
  assert.equal(toLocalInput(nextHour(new Date(2026, 9, 1, 10, 42))), "2026-10-01T11:00");
});

test("every status has words for the seller", () => {
  assert.equal(scheduleLabel("waiting"), "Waiting for the daily budget");
  assert.equal(scheduleLabel(undefined), "Scheduled");
});
