import { test } from "node:test";
import assert from "node:assert/strict";
import { EMPTY, boardGroups, boardOrder, clickSelect, coversChanged, dragged, moveProblem } from "./board.ts";
import type { Asset } from "./types.ts";

const order = ["a", "b", "c", "d", "e"];

test("a click picks one; clicking it again clears", () => {
  const one = clickSelect(EMPTY, "b", {}, order);
  assert.deepEqual(one.ids, ["b"]);
  assert.deepEqual(clickSelect(one, "c", {}, order).ids, ["c"]);
  assert.deepEqual(clickSelect(one, "b", {}, order).ids, []);
});

test("ctrl-click and a tap add or remove", () => {
  let s = clickSelect(EMPTY, "a", { toggle: true }, order);
  s = clickSelect(s, "c", { toggle: true }, order);
  assert.deepEqual(s.ids, ["a", "c"]);
  s = clickSelect(s, "a", { touch: true }, order);
  assert.deepEqual(s.ids, ["c"]);
});

test("shift-click picks the run from the last click, in either direction", () => {
  const s = clickSelect(EMPTY, "b", {}, order);
  assert.deepEqual(clickSelect(s, "d", { shift: true }, order).ids, ["b", "c", "d"]);
  const back = clickSelect(clickSelect(EMPTY, "d", {}, order), "a", { shift: true }, order);
  assert.deepEqual([...back.ids].sort(), ["a", "b", "c", "d"]);
});

test("a drag carries the selection only when it starts on a selected photo", () => {
  const s = { ids: ["a", "c"], anchor: "c" };
  assert.deepEqual(dragged(s, "c"), ["a", "c"]);
  assert.deepEqual(dragged(s, "e"), ["e"]);
});

const asset = (id: string, group: string, rank: number, sku: string | null = null): Asset =>
  ({ id, original_filename: `${id}.png`, parsed_sku: sku, group_key: group, rank, status: "processed" } as Asset);

test("groups: Unsorted apart, the rest by name, each in rank order", () => {
  const assets = [asset("x", "~unsorted", 1), asset("b2", "BR5229", 2, "BR5229"), asset("b1", "BR5229", 1, "BR5229"), asset("a1", "AB1234", 1, "AB1234")];
  const { tray, groups } = boardGroups(assets, new Set(["b1"]));
  assert.deepEqual(tray.assets.map((a) => a.id), ["x"]);
  assert.deepEqual(groups.map((g) => [g.key, g.sku, g.written]), [["AB1234", "AB1234", false], ["BR5229", "BR5229", true]]);
  assert.deepEqual(boardOrder(tray, groups), ["x", "a1", "b1", "b2"]);
  // Moving all of a written group out is refused before it is sent; its cover leaving is said.
  assert.match(moveProblem(["b1", "b2"], "AB1234", groups) ?? "", /BR5229 has a listing written/);
  assert.equal(moveProblem(["b1"], "AB1234", groups), null);
  assert.deepEqual(coversChanged(["b1"], "AB1234", groups), ["BR5229"]);
  assert.deepEqual(coversChanged(["b2"], "AB1234", groups), []);
});
