// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { KEEP_MS, confirmText, skuStore, type SkuOnEtsy } from "./skuOnEtsy.ts";

const ANSWER: SkuOnEtsy = {
  listings: [{ content_id: "c1", shops: [{ connection_id: "s1", shop_name: "PCS", etsy_listing_id: 1, current_sku: "CDM290", sku: "PCS-CDM290" }] }],
};

test("fifteen editors on one page ask once for the whole batch", async () => {
  const asked: string[] = [];
  let clock = 0;
  const store = skuStore(async (b) => (asked.push(b), ANSWER), () => clock);
  await Promise.all(Array.from({ length: 15 }, () => store.ensure("b1")));
  assert.deepEqual(asked, ["b1"]);
  assert.equal(store.drafts("b1", "c1")[0].sku, "PCS-CDM290");
  assert.equal(store.drafts("b1", "c2"), store.drafts("b1", "c3")); // stable empty answer: no render loop
  // Kept: rendering again asks nothing; after KEEP_MS it asks once more.
  await store.ensure("b1");
  assert.equal(asked.length, 1);
  clock = KEEP_MS + 1;
  await Promise.all([store.ensure("b1"), store.ensure("b1")]);
  assert.equal(asked.length, 2);
  // A saved SKU asks again at once, once.
  await store.forget("b1");
  assert.equal(asked.length, 3);
});

test("a failed read shows nothing to update and does not retry on every render", async () => {
  let n = 0;
  const store = skuStore(async () => { n++; throw new Error("offline"); }, () => 0);
  await store.ensure("b1");
  await store.ensure("b1");
  assert.equal(n, 1);
  assert.deepEqual(store.drafts("b1", "c1"), []);
});

test("the confirmation names the shops, how many listings change and the SKUs", () => {
  assert.equal(
    confirmText(ANSWER.listings[0].shops),
    "Change the SKU of 1 listing on Etsy in PCS (PCS: CDM290 → PCS-CDM290). Only the SKU changes; prices, quantities and variations stay.",
  );
  const two = confirmText([
    { connection_id: "a", shop_name: "OPA", etsy_listing_id: 1, current_sku: null, sku: "X1" },
    { connection_id: "b", shop_name: "ILC", etsy_listing_id: 2, current_sku: "X0", sku: "X1" },
  ]);
  assert.match(two, /^Change the SKU of 2 listings on Etsy in OPA and ILC \(OPA: no SKU → X1; ILC: X0 → X1\)/);
});

test("no screen asks the server to update SKUs on Etsy except the confirmation's button", () => {
  const editor = readFileSync(new URL("../components/SkuEditor.tsx", import.meta.url), "utf8");
  assert.equal(editor.match(/api\.updateSkuOnEtsy\(/g)?.length, 1);
  const effects = editor.split("useEffect(").slice(1).map((s) => s.split("}, [")[0]);
  assert.ok(effects.every((e) => !e.includes("updateSkuOnEtsy")), "an effect must never send it");
  const api = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
  assert.match(api, /skuOnEtsy: \(batchId: string\) => req<SkuOnEtsy>\(`\/batches\/\$\{batchId\}\/sku-etsy`\)/); // a GET
  for (const file of ["components/ReviewCard.tsx", "components/ShopWorkspace.tsx", "app/(app)/batches/[id]/page.tsx"]) {
    assert.ok(!readFileSync(new URL(`../${file}`, import.meta.url), "utf8").includes("updateSkuOnEtsy"), file);
  }
});
