// Overview's shop counts: a count not read from Etsy is never 0 ("reading…" or
// "not read yet", with when it was last read); a read count is the number.
import assert from "node:assert/strict";
import { test } from "node:test";
import { NOT_READ, READING, ago, countsNote, shouldPoll, shownCount } from "./shopCounts.ts";
import type { ShopSummary } from "./types.ts";

const NOW = Date.parse("2026-10-20T12:00:00Z");
const summary = (over: Partial<ShopSummary>): ShopSummary => ({
  app_published_this_month: 0, app_published_last_month: 0, shop_counts_known: false, syncing: false, total: 0,
  active: 0, draft: 0, published_this_month: 0, published_last_month: 0, fetched_at: null, stale: true,
  counts_read_at: null, ...over,
});

test("a shop just connected, being read: reading…, never 0, and the page looks again", () => {
  const s = summary({ syncing: true });
  assert.equal(shownCount(s, s.active), READING);
  assert.equal(countsNote(s, NOW), "Reading this shop's listings from Etsy…");
  assert.equal(shouldPoll(s), true);
});

test("counts never read and no read under way: not read yet", () => {
  const s = summary({});
  assert.equal(shownCount(s, s.draft), NOT_READ);
  assert.equal(countsNote(s, NOW), "Not read from Etsy yet; they are read shortly.");
  assert.equal(shouldPoll(s), false);
});

test("counts read too long ago to show: not read yet, with when they were last read", () => {
  const s = summary({ counts_read_at: "2026-10-19T07:00:00Z" });
  assert.equal(shownCount(s, 0), NOT_READ);
  assert.equal(countsNote(s, NOW), "Last read from Etsy 1 day ago, too long ago to show; they are read again shortly.");
});

test("counts read: the numbers, zeros included, and when", () => {
  const s = summary({ shop_counts_known: true, active: 0, draft: 4, total: 4, counts_read_at: "2026-10-20T09:00:00Z" });
  assert.equal(shownCount(s, s.active), 0); // a real zero: read from Etsy
  assert.equal(shownCount(s, s.draft), 4);
  assert.equal(countsNote(s, NOW), "Read from Etsy 3 hours ago.");
  assert.equal(shouldPoll(s), false);
});

test("still loading: nothing yet", () => {
  assert.equal(shownCount(null, 3), null);
  assert.equal(countsNote(undefined, NOW), null);
});

test("ago", () => {
  assert.equal(ago("2026-10-20T11:59:50Z", NOW), "just now");
  assert.equal(ago("2026-10-20T11:00:00Z", NOW), "1 hour ago");
  assert.equal(ago("2026-10-19T19:00:00Z", NOW), "17 hours ago");
});

test("Overview takes its shop counts from here, not from the listing copy's rows", async () => {
  const { readFileSync } = await import("node:fs");
  const page = readFileSync(new URL("../app/(app)/dashboard/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes("shownCount("), "the shop card and the rows use shownCount");
  assert.ok(!/listings\?\.length \?\? null/.test(page), "the Listings count is not the number of cached rows");
  assert.ok(!/\? "syncing" : "—"/.test(page), "the rows say reading… / not read yet");
});
