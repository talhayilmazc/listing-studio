// B7: the Shops table on a profile: by name, never by id; a shop without that name is skipped and said.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { applyToAll, copyFrom, planResult, type TableRow } from "./profileShopTable.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const opts = (pairs: [number, string][]) => pairs.map(([id, label]) => ({ id, label }));
const MAIN: TableRow = {
  shopId: "a", shopName: "OPA", main: true, settings: {}, sectionTitle: "Nurse Gifts",
  sections: ["Nurse Gifts", "Mugs"], prefix: "Comfort Colors®", daily: null,
};
const PCS: TableRow = {
  shopId: "b", shopName: "PCS", main: false,
  settings: { shipping_profile: { value: 11, options: opts([[11, "Regular"], [12, "MemoPaid"]]) } },
  sectionTitle: null, sections: ["nurse gifts"], prefix: "Comfort Colors®", daily: 5,
};
const BCT: TableRow = {
  shopId: "c", shopName: "BCT", main: false,
  settings: { shipping_profile: { value: 21, options: opts([[21, "Express"]]) } },
  sectionTitle: null, sections: null, prefix: null, daily: null,
};

test("apply a shipping profile to all shops by its name; a shop without it is skipped and said", () => {
  const source = { ...PCS, settings: { shipping_profile: { value: 12, options: opts([[11, "Regular"], [12, "MemoPaid"]]) } } };
  const withMemo = { ...BCT, settings: { shipping_profile: { value: 21, options: opts([[21, "Express"], [22, "memopaid"]]) } } };
  const plan = applyToAll("shipping_profile", source, [MAIN, source, withMemo]);
  assert.deepEqual(plan.actions, [{ kind: "pick", shopId: "c", resource: "shipping_profile", id: 22 }]);
  const none = applyToAll("shipping_profile", source, [MAIN, source, BCT]);
  assert.deepEqual(none.skipped, [{ shopName: "BCT", why: 'Shipping: no shipping setting named "MemoPaid"' }]);
  assert.equal(planResult(none), 'Nothing to change. Skipped BCT (Shipping: no shipping setting named "MemoPaid").');
});

test("a section goes by the shop's own title; a shop still reading its sections is skipped", () => {
  const plan = applyToAll("section", MAIN, [MAIN, PCS, BCT]);
  assert.deepEqual(plan.actions, [{ kind: "section", shopId: "b", title: "nurse gifts" }]);
  assert.deepEqual(plan.skipped, [{ shopName: "BCT", why: "Section: its sections are still being read" }]);
});

test("copy from shop: every column of one shop, the main shop's own settings are not carried", () => {
  const plan = copyFrom(MAIN, BCT);
  assert.deepEqual(plan.actions, [
    { kind: "prefix", shopId: "c", prefix: "Comfort Colors®" },
  ]);
  assert.equal(plan.skipped.length, 1); // its sections are still being read
  const back = copyFrom(PCS, { ...BCT, sections: ["Mugs"] });
  assert.deepEqual(back.actions.map((a) => a.kind), ["prefix", "daily"]);
  assert.ok(back.skipped.some((s) => s.why.startsWith("Shipping: no shipping setting")));
});

test("the profile card opens the table; every cell is changed in place", () => {
  const card = read("components/ProfileShops.tsx");
  assert.ok(card.includes("<ProfileShopTable"));
  const table = read("components/ProfileShopTable.tsx");
  for (const piece of ['data-testid="apply-to-all"', 'data-testid="copy-from"', "api.setProfileShopValue(", "api.setShopDailyLimit(", "api.pickInShop("]) {
    assert.ok(table.includes(piece), piece);
  }
  assert.ok(table.includes('className="overflow-x-auto'), "the wide table scrolls in its own box, never the page");
});
