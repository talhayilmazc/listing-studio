// The rail's "resets …" line is the reset instant in the seller's own time zone (Part F).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { resetTime } from "./resetTime.ts";

const RESET = "2026-12-01T06:00:00Z"; // midnight in Chicago (CST), the account's zone

test("the same instant, in each seller's clock", () => {
  assert.equal(resetTime(RESET, "America/Chicago"), "Dec 1, 12:00 AM CST");
  assert.equal(resetTime(RESET, "Europe/Istanbul"), "Dec 1, 9:00 AM GMT+3");
  assert.equal(resetTime(RESET, "Europe/London", { weekday: true }), "Tue, Dec 1, 6:00 AM GMT");
});

test("an unreadable time says nothing rather than a wrong time", () => {
  assert.equal(resetTime("not a date", "UTC"), "");
});

test("the rail and Settings use it, in the browser's zone, not the server's label", () => {
  const src = readFileSync(new URL("../components/Allowance.tsx", import.meta.url), "utf8");
  assert.ok(!src.includes("a.resets_label"), "the server's account-zone label is not shown");
  assert.ok(src.includes("resetTime(a.resets_at, browserZone()"));
});
