// B9: one consistent interface: the sidebar follows the work, one primary button per page,
// one-sentence explanations with "?" help, shared status chips, a first-run guide.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { steps } from "./firstRun.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("the sidebar: Upload, Batches, Scheduled, Analytics; Library; Shops & groups; Settings", () => {
  const rail = read("components/Rail.tsx");
  const labels = [...rail.matchAll(/label: "([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(labels, ["Overview", "Upload", "Batches", "Scheduled", "Analytics", "Profiles", "Mockup templates", "Shops & groups", "Settings", "Admin"]);
  assert.ok(rail.includes('title: "Library"') && rail.includes('title: "Account"'));
});

test("page titles use the same words as the sidebar; New upload is never the page's primary", () => {
  const strip = read("components/TopStrip.tsx");
  assert.ok(strip.includes('"Upload"]') && strip.includes('"Shops & groups"]') && !strip.includes('"Uploads"]'));
  assert.ok(strip.includes('className="btn-secondary shrink-0"') && !strip.includes("btn-primary"));
});

test("one primary button: the batch page and the review page hand it over as the work moves on", () => {
  const batch = read("app/(app)/batches/[id]/page.tsx");
  assert.ok(batch.includes('groups.every((g) => g.done) ? "btn-primary" : "btn-secondary"'));
  assert.ok(batch.includes('groups.some((g) => !g.done) ? "btn-primary" : "btn-secondary"'));
  assert.ok(read("app/(app)/batches/[id]/review/page.tsx").includes("primary={actions.approved === items.length}"));
});

test("one sentence first, the rest behind ?", () => {
  for (const page of ["app/(app)/profiles/page.tsx", "app/(app)/scheduled/page.tsx", "app/(app)/batches/[id]/review/page.tsx",
                      "app/(app)/analytics/page.tsx", "components/WhereAndWhen.tsx", "components/ProfileShopTable.tsx"]) {
    assert.ok(read(page).includes("<Help label="), page);
  }
  const help = read("components/Help.tsx");
  assert.ok(help.includes("aria-expanded") && !help.includes("onMouseEnter"), "help opens on a tap, never on hover only");
});

test("status words share one chip", () => {
  assert.ok(read("components/StatusPill.tsx").includes("<StatusChip"));
  assert.ok(read("app/(app)/scheduled/page.tsx").includes("<StatusChip"));
});

test("the first-run guide ticks each step from the account's own data", () => {
  const none = steps({ shops: 0, confirmedProfiles: 0, batches: 0, sent: 0 });
  assert.deepEqual(none.map((s) => [s.key, s.done]), [["shop", false], ["profile", false], ["upload", false], ["send", false]]);
  const half = steps({ shops: 2, confirmedProfiles: 1, batches: 0, sent: 0 });
  assert.deepEqual(half.filter((s) => s.done).map((s) => s.key), ["shop", "profile"]);
  assert.ok(read("app/(app)/dashboard/page.tsx").includes("<FirstRunGuide />"));
});

test("no single-shop leftovers in the words", () => {
  assert.ok(!read("components/MetricStrip.tsx").includes("open Overview to refresh"));
  assert.ok(!read("app/(app)/profiles/page.tsx").includes("switch shops at"));
});
