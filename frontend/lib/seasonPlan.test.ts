// The seasonal planner's page: our advice said as such, one button after everything is on screen, no ranking or
// sales promise, request counts never shown; the small sums the timeline uses.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { chosenShops, dayTitle, dayTone, firstChoice, monthHeads, planBody, toggled, windowLine, windowState, type PlannerState, type Season } from "./seasonPlan.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const PAGE = "app/(app)/season-planner/page.tsx";
const season = (over: Partial<Season> = {}): Season => ({ key: "x", name: "X", on: "2026-12-25", opens: "2026-10-11", closes: "2026-12-04", first_left: "2026-11-02", why: "w", ...over });

test("a window says where it stands today", () => {
  assert.equal(windowState(season(), "2026-11-02"), "Open now, 33 days left");
  assert.equal(windowState(season({ closes: "2026-11-02" }), "2026-11-02"), "Open now, 1 day left");
  assert.equal(windowState(season({ opens: "2026-12-01", first_left: "2026-12-01" }), "2026-11-02"), "Opens Dec 1");
  assert.equal(windowState(season({ first_left: null, closes: "2026-11-01" }), "2026-11-02"), "Window closed Nov 1");
  assert.equal(windowLine(season()), "Go live Oct 11 – Dec 4");
});

test("the timeline's months and a day's fullness", () => {
  assert.deepEqual(monthHeads(["2026-11-29", "2026-11-30", "2026-12-01"]), [{ label: "Nov", span: 2 }, { label: "Dec", span: 1 }]);
  const row = { cap: 3, limit: 4 };
  assert.equal(dayTone({ day: "d", new: 0, taken: 0 }, row), "empty");
  assert.equal(dayTone({ day: "d", new: 0, taken: 2 }, row), "taken");
  assert.equal(dayTone({ day: "d", new: 1, taken: 1 }, row), "some");
  assert.equal(dayTone({ day: "d", new: 3, taken: 0 }, row), "full");
  assert.equal(dayTone({ day: "d", new: 1, taken: 3 }, row), "full");
  assert.equal(dayTitle({ day: "2026-11-02", new: 1, taken: 2 }, "Frost"), "Frost, Nov 2: 1 new, 2 already scheduled");
});

const state: PlannerState = {
  today: "2026-11-02", reviewed: "2026-11-02", about: "a", time_zone: "UTC", batches: [],
  seasons: [season({ key: "closed", first_left: null }), season({ key: "open" })],
  shops: [{ id: "s1", name: "One" }, { id: "s2", name: "Two" }], groups: [{ id: "g", name: "G", shop_ids: ["s2"] }],
  defaults: { per_shop_per_day: 5, window_start: "09:00:00", window_end: "17:00:00", spacing_minutes: 30, stagger_minutes: 5 },
};

test("the first choice is the next open window, and no shop or batch is ticked for the seller", () => {
  const choice = firstChoice(state);
  assert.equal(choice.holiday, "open");
  assert.deepEqual([choice.batch_ids, choice.connection_ids, choice.group_ids], [[], [], []]);
  assert.deepEqual(firstChoice({ ...state, shops: [state.shops[0]] }).connection_ids, ["s1"]);
  assert.equal(planBody(choice).window_start, "09:00");
});

test("a group and a shop reach each shop once", () => {
  assert.deepEqual([...chosenShops(state, { connection_ids: ["s2", "s1"], group_ids: ["g"] })].sort(), ["s1", "s2"]);
  assert.deepEqual(toggled(toggled(["a"], "b"), "a"), ["b"]);
});

test("one save, and only with the fingerprint of what is on screen", () => {
  const page = read(PAGE);
  assert.equal(page.match(/api\.seasonSave\(/g)?.length, 1);
  assert.ok(page.includes("fingerprint: preview.fingerprint"));
  assert.ok(page.includes('disabled={busy || !!preview.disabled || !preview.fingerprint}'));
  assert.equal(page.match(/className="btn-primary"/g)?.length, 1, "one primary button");
  for (const shown of ['data-testid="season-timeline"', 'data-testid="season-conflicts"', 'data-testid="season-not-fitting"', 'data-testid="season-left-out"']) assert.ok(page.includes(shown), shown);
});

test("the windows are labelled as our advice, and promise nothing", () => {
  const page = read(PAGE);
  assert.ok(page.includes("Our advice") && page.includes('data-testid="season-about"'));
  const calendar = readFileSync(new URL("../../backend/app/pipeline/season_calendar.py", import.meta.url), "utf8");
  assert.ok(calendar.includes('"Our advice, not Etsy\'s') && calendar.includes("REVIEWED = date("));
  assert.ok(!/rank|more sales|more views|boost|guarantee/i.test(page + calendar + read("lib/seasonPlan.ts")));
  assert.ok(!/<h1/.test(page), "the top strip names the page");
});

test("the timeline scrolls in its own box; the page does not", () => {
  assert.ok(read(PAGE).includes('className="overflow-x-auto" data-testid="season-timeline"'));
});
