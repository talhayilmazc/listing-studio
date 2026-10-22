// B8: the Scheduled page: filters, the timeline by day, selection per row, day, group and shop.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { BULK_LABELS, allIn, byDay, dragged, filterItems, groupLine, movableKeys, toggleMany, wallValue, type ScheduledItem } from "./scheduled.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const item = (key: string, at: string, shop: string, extra: Partial<ScheduledItem> = {}): ScheduledItem => ({
  key, kind: "planned", content_id: null, title: key, sku: null, batch_id: null, batch_name: null, shop_id: shop,
  shop_name: shop, group_ids: [], profile_id: null, profile_name: null, at, local: at, local_day: at.slice(0, 10),
  local_time: at.slice(11, 16), status: "planned", note: null, movable: true, paused: false, listing_link: null, ...extra,
});
const ITEMS = [
  item("b", "2026-10-23T10:00", "PCS", { profile_id: "p2" }),
  item("a", "2026-10-22T09:00", "OPA", { profile_id: "p1" }),
  item("c", "2026-10-22T11:00", "PCS", { status: "published", movable: false }),
];
const GROUPS = [{ id: "g", name: "G1", shop_ids: ["PCS"], settings: null, paused: false, upcoming: 1, first_local: "x", last_local: "x" }];

test("the timeline is by day, in time order", () => {
  assert.deepEqual(byDay(ITEMS).map(([d, l]) => [d, l.map((i) => i.key)]), [["2026-10-22", ["a", "c"]], ["2026-10-23", ["b"]]]);
  assert.equal(wallValue(ITEMS[0]), "2026-10-23T10:00");
});

test("filters by group, shop, profile and status", () => {
  const none = { group: "", shop: "", profile: "", status: "" };
  assert.deepEqual(filterItems(ITEMS, { ...none, group: "g" }, GROUPS).map((i) => i.key), ["b", "c"]);
  assert.deepEqual(filterItems(ITEMS, { ...none, profile: "p1" }, GROUPS).map((i) => i.key), ["a"]);
  assert.deepEqual(filterItems(ITEMS, { ...none, status: "published" }, GROUPS).map((i) => i.key), ["c"]);
});

test("selection: only rows that can move; a day or everything shown at once; a drag moves the selection", () => {
  assert.deepEqual(movableKeys(ITEMS), ["b", "a"]);
  const day = movableKeys(byDay(ITEMS)[0][1]);
  let selected = toggleMany([], day, true);
  assert.ok(allIn(selected, day));
  selected = toggleMany(selected, ["b"], true);
  assert.deepEqual(dragged(selected, "b"), ["a", "b"]);
  assert.deepEqual(dragged(selected, "z"), ["z"]);
  assert.equal(groupLine({ ...GROUPS[0], upcoming: 0 }), "Nothing scheduled");
});

test("the page: summaries, filters, bulk actions with a preview, one Save, Undo, pause, Schedule listings", () => {
  assert.deepEqual(Object.values(BULK_LABELS), ["Set time…", "Shift by…", "Spread between…", "Move to day…", "Publish now", "Remove from schedule"]);
  const page = read("app/(app)/scheduled/page.tsx");
  for (const piece of ['data-testid="group-summaries"', 'data-testid="scheduled-filters"', 'data-testid="bulk-preview"',
                       'data-testid="bulk-save"', 'data-testid="undo"', 'data-testid="pause-all"', 'data-testid="group-pause"',
                       'data-testid="reflow-preview"', 'data-testid="schedule-listings"', 'data-testid="row-time"',
                       'data-testid="select-day"', 'data-testid="select-all-shown"', "onDrop=", "<WhereAndWhen"]) {
    assert.ok(page.includes(piece), piece);
  }
  assert.equal(page.match(/className="btn-primary"/g)?.length, 1, "one primary button on the page");
  for (const gone of ["Split evenly", "requests"]) assert.ok(!page.includes(gone), gone);
});
