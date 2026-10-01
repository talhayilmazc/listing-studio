// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { change, changeLabel, changeTone, filterRows, listingName, money, percent } from "./analytics.ts";
import type { Economics, ListingClass, ListingRow } from "./types.ts";

function econ(e: Partial<Economics>): Economics {
  return {
    units: 0, orders: 0, revenue: 0, fees: { listing_fees: 0, transaction_fees: 0, processing_fees: 0 }, fees_total: 0,
    fees_source: "rates", product: null, unit_cost: null, unit_cost_source: null, shipping: null, ads: null,
    ad_orders: null, ad_revenue: null, net: 0, margin: null, net_per_unit: null, net_excludes: [], contribution: 0,
    break_even_acos: null, acos: null, spend_per_sale: null, ...e,
  };
}

function row(id: number, status: ListingClass, e: Partial<Economics> | null, stake?: number, title: string | null = `T${id}`): ListingRow {
  return {
    listing_id: id, title, state: "active", url: "", thumbnail_url: null, sku: `SKU${id}`, profile_name: null, launched: null,
    economics: e === null ? null : econ(e), comparison: null, trend: null, status,
    action: stake === undefined ? null : { listing_id: id, kind: "fading", stake, reason: "", action: "", links: [] },
  };
}

test("money is formatted from minor units; a missing figure is a dash, not zero", () => {
  assert.equal(money(4700, "USD"), "$47.00");
  assert.equal(money(-3100, "USD"), "-$31.00");
  assert.equal(money(125050, "EUR"), "€1,250.50");
  assert.equal(money(null, "USD"), "—");
  assert.equal(percent(0.25), "25%");
});

test("changes carry their sign and say when there's nothing to compare", () => {
  assert.equal(changeLabel(change(112, 100), 112), "+12%");
  assert.equal(changeLabel(-0.08, 10), "−8%");
  assert.equal(changeLabel(null, 5), "new");
  assert.equal(changeLabel(null, 0), "—");
  assert.equal(change(null, 100), null);
  assert.equal(changeTone(-0.3, false), "up"); // less spent is good news
});

test("the table ranks by money at stake, filters, and sorts on any figure", () => {
  const rows = [
    row(1, "fading", { revenue: 1000, net: 200 }, 8000),
    row(2, "winner", { revenue: 20000, net: 9000 }, undefined, "Funny Nurse Tee"),
    row(3, "steady", { revenue: 3000, net: 800 }, 500, null),
    row(4, "new", null),
  ];
  assert.deepEqual(filterRows(rows, {}).map((r) => r.listing_id), [1, 3, 2, 4]);
  assert.deepEqual(filterRows(rows, { sort: "net" }).map((r) => r.listing_id), [2, 3, 1, 4]);
  assert.deepEqual(filterRows(rows, { classes: ["winner", "steady"] }).map((r) => r.listing_id), [3, 2]);
  assert.deepEqual(filterRows(rows, { q: "nurse" }).map((r) => r.listing_id), [2]);
  assert.equal(listingName(rows[2]), "Listing 3");
});
