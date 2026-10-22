// B6: every shop's profiles in the sidebar, grouped by main shop with a search above ten; the
// Profiles page for all shops with a shop filter and a per-shop breakdown.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { SEARCH_ABOVE, byMainShop, inShop, perShop } from "./profileShops.ts";
import { supportsAll } from "./shopScope.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const SHOPS = [{ id: "a", name: "OPA" }, { id: "b", name: "PCS" }];
const p = (id: string, name: string, shop: string, links: string[] = [], confirmed = true) => ({
  id, name, content_template: "apparel", connection_id: shop, shop_name: null, confirmed,
  links: links.map((connection_id) => ({ connection_id })),
});
const PROFILES = [p("1", "Regular", "b"), p("2", "Comfort", "a", ["b"]), p("3", "Hoodie", "a"), p("4", "Draft", "b", [], false)];

test("grouped by main shop in the account's shop order, names sorted", () => {
  assert.deepEqual(byMainShop(PROFILES, SHOPS).map(([shop, items]) => [shop, items.map((x) => x.name)]),
    [["OPA", ["Comfort", "Hoodie"]], ["PCS", ["Draft", "Regular"]]]);
  assert.deepEqual(byMainShop(PROFILES, SHOPS, "reg").map(([shop]) => shop), ["PCS"]);
  assert.equal(SEARCH_ABOVE, 10);
});

test("a shop filter keeps the shop's own profiles and those set up there", () => {
  assert.deepEqual(PROFILES.filter((x) => inShop(x, "b")).map((x) => x.name), ["Regular", "Comfort", "Draft"]);
  assert.equal(PROFILES.filter((x) => inShop(x, null)).length, 4);
  assert.deepEqual(perShop(PROFILES, SHOPS), [
    { id: "a", name: "OPA", main: 2, setUp: 0 },
    { id: "b", name: "PCS", main: 1, setUp: 1 },
  ]);
});

test("Profiles shows every shop; the sidebar lists every shop's profiles in a section that opens and closes", () => {
  assert.ok(supportsAll("/profiles"));
  const page = read("app/(app)/profiles/page.tsx");
  assert.ok(page.includes('data-testid="profiles-shop-filter"') && page.includes("All shops") && page.includes('data-testid="profiles-by-shop"'));
  assert.ok(!page.includes("switch shops at"), "no single-shop leftover");
  const rail = read("components/Rail.tsx");
  assert.ok(rail.includes("api\n        .listProfiles(null)") || rail.includes(".listProfiles(null)"));
  assert.ok(rail.includes('data-testid="rail-profiles-toggle"') && rail.includes("profiles.length > SEARCH_ABOVE") && rail.includes("byMainShop("));
});
