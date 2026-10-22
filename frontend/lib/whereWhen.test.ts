// B4: one "Where and when" panel replaces the review matrix and "Send to shop groups and schedule";
// every approved listing goes to every chosen shop (the owner, 2026-10-22: no splitting).
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { test } from "node:test";
import {
  SETTINGS_LINE,
  WHEN_LABELS,
  cellLine,
  clock,
  destinationLabel,
  destinationOptions,
  rememberWhen,
  rememberedWhen,
  scheduleLine,
  startingToday,
  toggle,
} from "./whereWhen.ts";
import type { SendState } from "./types.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

const STATE: SendState = {
  shops: [{ id: "s1", name: "Frost Tees" }, { id: "s2", name: "Frost Mugs" }, { id: "s3", name: "Gift Den" }],
  groups: [{ id: "g1", name: "GROUP 1", shop_ids: ["s1", "s2"] }, { id: "g2", name: "Gifts", shop_ids: ["s3"] }],
  send_to: null,
  label: "Frost Tees",
  offer_all_shops: false,
};

test("the three choices and the one settings line, as written", () => {
  assert.deepEqual(Object.values(WHEN_LABELS), ["Now as drafts", "Publish now", "Spread out"]);
  assert.equal(SETTINGS_LINE, "Each shop uses the profile's shipping, return and processing settings automatically.");
});

test("spread out is one summary line", () => {
  const s = startingToday("2026-10-22", null);
  assert.equal(scheduleLine(s), "From Oct 22 · 5 a day per shop · 9:00 AM–5:00 PM · 30 min apart");
  assert.equal(clock("00:30"), "12:30 AM");
  assert.equal(clock("12:00"), "12:00 PM");
});

test("groups first, then single shops; a search narrows both", () => {
  assert.deepEqual(destinationOptions(STATE, "").groups.map((g) => g.name), ["GROUP 1", "Gifts"]);
  const hit = destinationOptions(STATE, "gift");
  assert.deepEqual([hit.groups.map((g) => g.name), hit.shops.map((s) => s.name)], [["Gifts"], ["Gift Den"]]);
});

test("several groups and shops can be ticked; nothing ticked is the default again", () => {
  let dest = toggle(null, "group", "g1");
  dest = toggle(dest, "shop", "s3");
  assert.deepEqual(dest, { group_ids: ["g1"], connection_ids: ["s3"] });
  assert.equal(destinationLabel(dest, STATE, "x"), "GROUP 1 + Gift Den");
  assert.equal(toggle(toggle(dest, "group", "g1"), "shop", "s3"), null);
  assert.equal(destinationLabel(null, STATE, "Frost Tees"), "Frost Tees");
});

test("each shop of each listing in the list: its time, or why not", () => {
  const base = { shop_id: "s1", shop_name: "Frost Tees", reason: null, at: null, local: null };
  assert.equal(cellLine({ ...base, state: "new", at: "x", local: "Oct 22, 9:00 AM CDT" }), "Frost Tees · Oct 22, 9:00 AM CDT");
  assert.equal(cellLine({ ...base, state: "new" }), "Frost Tees · new draft");
  assert.equal(cellLine({ ...base, state: "draft" }), "Frost Tees · already a draft");
  assert.equal(cellLine({ ...base, state: "cannot", reason: "no profile" }), "Frost Tees · no profile");
  assert.equal(cellLine({ ...base, state: "new", note: "title rewritten once to fit this shop" }),
    "Frost Tees · new draft · title rewritten once to fit this shop");
});

test("the last When is remembered in this browser, and storage failing is fine", () => {
  const store = new Map<string, string>();
  (globalThis as any).localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) };
  assert.equal(rememberedWhen().when, "drafts");
  rememberWhen("spread", { ...startingToday("2026-10-22", null), per_shop_per_day: 3 });
  assert.deepEqual([rememberedWhen().when, rememberedWhen().schedule?.per_shop_per_day], ["spread", 3]);
  (globalThis as any).localStorage = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.equal(rememberedWhen().when, "drafts");
  rememberWhen("publish", null);
  delete (globalThis as any).localStorage;
});

test("the review page has one Where and when panel; the matrix and the split are gone", () => {
  const page = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(page.includes("<WhereAndWhen"));
  for (const gone of ["PublishMatrix", "<Distribution", "BulkSchedule", "Create drafts for all", "Schedule…", "tickShop", "publishPreview"]) {
    assert.ok(!page.includes(gone), gone);
  }
  assert.ok(!existsSync(new URL("../components/PublishMatrix.tsx", import.meta.url)));
  assert.ok(!existsSync(new URL("../components/Distribution.tsx", import.meta.url)));
  const panel = read("components/WhereAndWhen.tsx") + read("components/DestinationPicker.tsx") + read("lib/whereWhen.ts");
  for (const gone of ["Split evenly", "Tick a group for every listing", "written for", "duplicate shops", "estimated_calls", "requests"]) {
    assert.ok(!panel.includes(gone), gone);
  }
  // One button, labelled by the server ("Create 12 drafts"), with the reason when it is disabled.
  const own = read("components/WhereAndWhen.tsx");
  assert.equal(own.match(/btn-primary/g)?.length, 1);
  assert.ok(own.includes("preview?.button") && own.includes('data-testid="send-disabled"'));
  // A one-shop seller sees the shop's name, no picker and no groups.
  assert.ok(own.includes('data-testid="send-to-one"') && own.includes("state.shops.length === 1"));
  assert.ok(own.includes("Make a group of all my shops") && own.includes("Show each listing"));
  // Groups first, "or one shop", and a link to create groups.
  const picker = read("components/DestinationPicker.tsx");
  assert.ok(picker.indexOf(">Groups<") < picker.indexOf(">or one shop<") && picker.includes("Create shop groups"));
});

test("a card changes its profile in place and says where it goes", () => {
  const card = read("components/ReviewCard.tsx");
  assert.ok(!card.includes("change on its group"));
  assert.ok(card.includes("<ProfilePicker") && card.includes('data-testid="card-destination"'));
});
