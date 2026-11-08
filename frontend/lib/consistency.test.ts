// One consistent interface (B9, app/onboarding-v1): the sidebar names the work in the seller's
// words, one primary button per page, one-sentence explanations with "?" help, shared status
// chips, and a first-run checklist kept on the server.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { offersExamples, stateOf, viewOf } from "./onboarding.ts";
import { tabsFor } from "./sectionTabs.ts";
import type { Onboarding } from "./types.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("the sidebar: six entries named by what the seller does, then Settings", () => {
  const rail = read("components/Rail.tsx");
  const labels = [...rail.matchAll(/label: "([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(labels, ["Home", "Designs", "Review", "Schedule", "Reports", "Shops", "Settings", "Admin"]);
  assert.ok(!rail.includes('title: "Library"') && !rail.includes('title: "Workspace"'), "no section headings: six entries need none");
  // What moved under an entry is a tab there, one click away.
  assert.equal(tabsFor("/profiles")?.current, "/profiles");
  assert.deepEqual(tabsFor("/settings")?.tabs.map((t) => t.label), ["Account", "Profiles", "Mockup templates"]);
  assert.deepEqual(tabsFor("/analytics")?.tabs.map((t) => t.label), ["Profit and sales", "Listing health"]);
  assert.deepEqual(tabsFor("/scheduled")?.tabs.map((t) => t.label), ["Scheduled", "Season planner"]);
  assert.equal(tabsFor("/dashboard"), null);
});

test("page titles use the same words as the sidebar; no button in the header", () => {
  const strip = read("components/TopStrip.tsx");
  for (const word of ['"Home"]', '"Designs"]', '"Review"]', '"Schedule"]', '"Reports"]', '"Shops"]', '"Settings"]', '"Upload designs"]']) {
    assert.ok(strip.includes(word), word);
  }
  assert.ok(!strip.includes("btn-primary") && !strip.includes("btn-secondary") && !strip.includes("New upload"));
});

test("one primary button: the batch page and the review page hand it over as the work moves on", () => {
  const batch = read("app/(app)/batches/[id]/page.tsx");
  assert.ok(batch.includes('groups.every((g) => g.done) ? "btn-primary" : "btn-secondary"'));
  assert.ok(batch.includes('"Write listings"'));
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

const step = (key: Onboarding["steps"][number]["key"], done: boolean) =>
  ({ key, title: key, body: "", href: "/", action: key, done, done_at: null });

test("the first-run checklist comes from the server and becomes one note once done", () => {
  const fresh: Onboarding = { steps: [step("shop", false), step("upload", false), step("approve", false), step("send", false)],
    next: "shop", all_done: false, dismissed: false, example_batch_id: null };
  assert.equal(viewOf(fresh), "steps");
  assert.deepEqual(fresh.steps.map((s) => stateOf(s, fresh)), ["now", "later", "later", "later"]);
  assert.ok(offersExamples(fresh));
  const done: Onboarding = { ...fresh, steps: fresh.steps.map((s) => ({ ...s, done: true })), next: null, all_done: true };
  assert.equal(viewOf(done), "set_up");
  assert.equal(viewOf({ ...done, dismissed: true }), "none");
  assert.equal(viewOf(null), "none");
  assert.ok(!offersExamples(done));
  const home = read("app/(app)/dashboard/page.tsx");
  assert.ok(home.includes("<SetupChecklist />") && !home.includes("FirstRunGuide"));
  // The empty slot for a usage line: there, and rendering nothing.
  const card = read("components/SetupChecklist.tsx");
  assert.match(card, /<UsageLine \/>/);
  assert.ok(card.includes("function UsageLine(): null {") && card.includes("  return null;"), "the usage line renders nothing yet");
});

test("no single-shop leftovers in the words", () => {
  assert.ok(!read("components/MetricStrip.tsx").includes("open Overview to refresh"));
  assert.ok(!read("app/(app)/profiles/page.tsx").includes("switch shops at"));
});
