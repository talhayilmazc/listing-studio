// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { changeLabel, changeTone, filterRows, listingName, money, percent } from "./analytics.ts";
import type { AnalyticsRow, Figures, ListingClass } from "./types.ts";

const zero: Figures = {
  units: 0, orders: 0, revenue: 0, transaction_fee: 0, payment_fee: 0, listing_fee: 0, fees: 0,
  product_cost: 0, shipping_cost: 0, ad_spend: 0, ad_orders: 0, ad_revenue: 0, costs: 0, net: 0,
  margin: null, aov: null, acos: null,
};

function row(id: number, klass: ListingClass, current: Partial<Figures>, title: string | null = `T${id}`): AnalyticsRow {
  return {
    listing_id: id, title, state: "active", url: "", thumbnail_url: null, sku: `SKU${id}`, profile_name: null,
    current: { ...zero, ...current }, previous: zero, revenue_change: null, net_change: null, units_change: null,
    verdict: { klass, reason: "", action: "", links: [] },
  };
}

test("money is formatted from minor units in the shop's currency", () => {
  assert.equal(money(4700, "USD"), "$47.00");
  assert.equal(money(-3100, "USD"), "-$31.00");
  assert.equal(money(125050, "EUR"), "€1,250.50");
  assert.equal(money(null, "USD"), "—");
  assert.equal(percent(0.5125, 1), "51.2%");
  assert.equal(percent(0.25), "25%");
});

test("changes against the previous period carry their sign", () => {
  assert.equal(changeLabel(0.124, 10), "+12%");
  assert.equal(changeLabel(-0.08, 10), "−8%");
  assert.equal(changeLabel(null, 5), "new");
  assert.equal(changeLabel(null, 0), "—");
  assert.equal(changeTone(-0.3), "down");
  assert.equal(changeTone(-0.3, false), "up"); // less ad spend is good news
});

test("the table filters by class and text and sorts by any figure", () => {
  const rows = [
    row(1, "ad_sink", { net: -3100, ad_spend: 4700 }),
    row(2, "winner", { net: 9000, revenue: 20000 }, "Funny Nurse Tee"),
    row(3, "steady", { net: 800, revenue: 3000 }, null),
  ];
  assert.deepEqual(filterRows(rows, {}).map((r) => r.listing_id), [1, 2, 3]); // server order
  assert.deepEqual(filterRows(rows, { sort: "net" }).map((r) => r.listing_id), [2, 3, 1]);
  assert.deepEqual(filterRows(rows, { sort: "net", desc: false }).map((r) => r.listing_id), [1, 3, 2]);
  assert.deepEqual(filterRows(rows, { classes: ["winner", "steady"] }).map((r) => r.listing_id), [2, 3]);
  assert.deepEqual(filterRows(rows, { q: "nurse" }).map((r) => r.listing_id), [2]);
  assert.deepEqual(filterRows(rows, { q: "sku3" }).map((r) => r.listing_id), [3]);
  assert.equal(listingName(rows[2]), "Listing 3");
});
