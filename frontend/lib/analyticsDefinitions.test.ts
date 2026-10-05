// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { BASIS, CLASSES, COLUMNS, LINES, SECTIONS, TOTALS, document } from "./analyticsDefinitions.ts";

// The lines the month endpoint sends (backend/app/pipeline/pnl.py: SECTIONS).
const SENT: Record<string, string[]> = {
  revenue: ["items", "shipping_paid", "refunds"],
  etsy_fees: ["transaction_fee_items", "transaction_fee_shipping", "processing_fee", "listing_fee", "fee_credits", "other_fees", "fee_taxes"],
  marketing: ["etsy_ads", "offsite_ads", "other_marketing"],
  other: ["shipping_labels", "unrecognised"],
  your_costs: ["product_cost", "provider_shipping"],
};

test("every section and line the month view sends has a definition", () => {
  for (const [section, lines] of Object.entries(SENT)) {
    assert.ok(SECTIONS[section], `section ${section}`);
    for (const line of lines) assert.ok(LINES[line]?.text.length > 20, `line ${line}`);
  }
  assert.deepEqual(Object.keys(LINES).sort(), Object.values(SENT).flat().sort());
  for (const key of ["net_etsy", "profit", "deposits", "ads_charged", "ads_clicks", "break_even_roas", "actual_roas", "not_attributed"]) assert.ok(TOTALS[key]);
  assert.deepEqual(Object.keys(BASIS), ["exact", "calculated", "estimated"]);
  assert.deepEqual(Object.keys(CLASSES).sort(), ["fading", "losing", "new", "steady", "winner"]);
  assert.ok(COLUMNS.result.text.includes("Before Etsy Ads"));
});

test("Etsy Ads is defined as a shop-level cost that is never given to listings", () => {
  assert.match(LINES.etsy_ads.text, /not given to listings, not even in proportion/);
  assert.equal(TOTALS.ads_charged.label, "Charged by Etsy this month");
  assert.equal(TOTALS.ads_clicks.label, "Ad spend for clicks this month");
});

test("docs/analytics.md is this file's text (run: node scripts/analytics-doc.mjs)", (t) => {
  const file = path.join(import.meta.dirname, "..", "..", "docs", "analytics.md");
  if (!fs.existsSync(file)) return t.skip("docs/ is not here (a container sees only frontend/)");
  assert.equal(fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n"), document());
});
