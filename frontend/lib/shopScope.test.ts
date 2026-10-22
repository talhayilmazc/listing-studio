// The shop switcher: "All shops" by default with several shops, one shop narrows it,
// and a shop in two groups is listed under both.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ALL, resolveScope, shopSections, supportsAll } from "./shopScope.ts";

const shops = [{ id: "a", group_ids: ["A"] }, { id: "b", group_ids: ["A", "B"] }, { id: "c", group_ids: [] }];

test("bug 2: with several shops the view is every shop until one is picked", () => {
  assert.deepEqual(resolveScope(shops, null, null), { allShops: true, selected: shops[0] }); // nothing remembered
  assert.deepEqual(resolveScope(shops, ALL, "b"), { allShops: true, selected: shops[1] }); // pages on one shop keep "b"
  assert.deepEqual(resolveScope(shops, "c", "c"), { allShops: false, selected: shops[2] }); // the switcher narrows
  assert.deepEqual(resolveScope([shops[0]], null, null), { allShops: false, selected: shops[0] }); // one shop: that shop
  assert.deepEqual(resolveScope(shops, "gone", null), { allShops: true, selected: shops[0] }); // a disconnected shop
});

test("All shops is offered only on pages that can total every shop", () => {
  // Profiles shows every shop's profiles with a shop filter (B6).
  for (const page of ["/dashboard", "/analytics", "/batches", "/scheduled", "/profiles"]) assert.ok(supportsAll(page), page);
  for (const page of ["/upload", "/batches/x/review", "/templates"]) assert.ok(!supportsAll(page), page);
});

test("a shop in two groups is listed under each; shops in no group come last", () => {
  const sections = shopSections(shops, [{ id: "A", name: "Group A" }, { id: "B", name: "Group B" }]);
  assert.deepEqual(sections.map(([name, members]) => [name, members.map((s) => s.id)]), [
    ["Group A", ["a", "b"]], ["Group B", ["b"]], ["No group", ["c"]],
  ]);
  assert.deepEqual(shopSections(shops, [])[0][1].length, 3);
});
