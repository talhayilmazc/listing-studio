// Listing health: the sales line only for shops never read, "Read sales for all", the refresh when a read ends,
// Home's card; "Keep it, I know the risk" only for brand names (fix/health-sales-and-brand).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  estimateLine, homeSalesCard, KEEP_BRAND, KEEP_LABEL, KEPT_NOTE, offersKeep, progressLine, readAllLabel, readEnded, salesMoving,
  WARN_AGAIN, type ShopSales,
} from "./listingHealth.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const server = readFileSync(new URL("../../backend/app/pipeline/listing_health.py", import.meta.url), "utf8");

const shop = (over: Partial<ShopSales> = {}): ShopSales => ({
  connection_id: "a", shop_name: "Frost Tees", state: "never", known: false, needs_read: true, waiting_turn: false,
  window_count: null, days_estimate: null, read_count: 0, resumes_at: null, note: null, ...over,
});

test("the line is only for a shop whose sales were never read, and it never says an Etsy request count", () => {
  const component = read("components/HealthSales.tsx");
  assert.ok(component.includes("if (!here || here.known) return null"), "a shop whose sales are read shows nothing");
  assert.ok(component.includes("here.needs_read &&"), "the button only for a shop that needs it");
  assert.equal(estimateLine(shop()), "", "nothing counted yet: no estimate made up");
  assert.equal(estimateLine(shop({ window_count: 900, days_estimate: 1 })), "About 900 sales in the last 13 months to read; it finishes today.");
  assert.equal(estimateLine(shop({ window_count: 40000, days_estimate: 3 })), "About 40,000 sales in the last 13 months to read; it takes about 3 days.");
  for (const text of [component, estimateLine(shop({ window_count: 9, days_estimate: 2 }))]) assert.ok(!/request/i.test(text), "no Etsy request count to the seller (A3)");
});

test("Read sales for all: one button for every shop that needs it, read one after another", () => {
  assert.equal(readAllLabel(11), "Read sales for all 11 shops");
  const component = read("components/HealthSales.tsx");
  assert.ok(component.includes("api.healthReadSales(ids)") && component.includes("read([])"), "empty: every shop never read");
  assert.equal(progressLine(shop({ state: "queued", waiting_turn: true })), "Waiting for its turn: your shops are read one after another.");
  assert.equal(progressLine(shop({ state: "reading", read_count: 1200, window_count: 3000 })), "Reading sales: 1,200 of 3,000");
  assert.equal(progressLine(shop({ state: "complete", known: true })), "");
  assert.ok(["queued", "counting", "reading"].every((state) => salesMoving(shop({ state: state as ShopSales["state"] }))));
  assert.ok(!salesMoving(shop({ state: "waiting" })) && !salesMoving(shop({ state: "complete" })));
});

test("when this shop's read ends, Listing health reads its buckets again by itself", () => {
  assert.ok(readEnded(shop({ state: "reading" }), shop({ state: "complete", known: true, needs_read: false })));
  assert.ok(!readEnded(shop({ state: "reading" }), shop({ state: "reading", read_count: 300 })));
  assert.ok(!readEnded(undefined, shop({ known: true })), "the first look is not an ending");
  const page = read("app/(app)/listing-health/page.tsx");
  assert.ok(page.includes("if (readEnded(was, now)) void load();"));
  assert.ok(page.includes("api.healthSales().then(takeSales)") && page.includes("salesLive"), "it looks again while a read moves");
});

test("opening the page with a stale copy says the shop is being read, and a failed sync is not asked every three seconds", () => {
  const page = read("app/(app)/listing-health/page.tsx");
  assert.ok(page.includes('"Reading your shop…"'));
  assert.ok(page.includes("slow ? 30000 : 3000"));
});

test("Home: a small card while some shop's sales were never read, linking to Listing health", () => {
  assert.equal(homeSalesCard(null), null);
  assert.equal(homeSalesCard({ to_read: 0 }), null);
  const one = homeSalesCard({ to_read: 1 })!;
  assert.equal(one.href, "/listing-health");
  assert.match(one.text, /One of your shops/);
  assert.match(homeSalesCard({ to_read: 12 })!.text, /^12 of your shops/);
  const home = read("app/(app)/dashboard/page.tsx");
  assert.ok(home.includes('{fresh ? null : <SalesNotReadCard key="sales-not-read" />}'), "only with a shop connected");
});

test("Keep it, I know the risk: only the brand problem offers it, with a way back", () => {
  assert.ok(offersKeep({ id: "brand", fixes: ["rewrite", "edit", KEEP_BRAND] }));
  for (const id of ["title_rejected", "tags_rejected", "main_photo", "required_attribute", "stray_characters", "stopped_selling"]) {
    assert.ok(!offersKeep({ id, fixes: [KEEP_BRAND] }), id);
  }
  assert.equal(KEEP_LABEL, "Keep it, I know the risk");
  assert.equal(KEPT_NOTE, "Brand name kept by you");
  assert.equal(WARN_AGAIN, "Show the warning again");
  assert.ok(server.includes(`KEEP_BRAND = "${KEEP_BRAND}"`), "the same action name on the server");
  // The server offers it on the brand problem only.
  const offering = [...server.matchAll(/Problem\("([a-z_]+)"[^\n]*KEEP_BRAND/g)].map((m) => m[1]);
  assert.deepEqual(offering, ["brand"]);
  const page = read("app/(app)/listing-health/page.tsx");
  assert.ok(page.includes("listing.problems.some(offersKeep)") && page.includes("onKeep(true)"));
});
