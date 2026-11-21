// A batch published in one shop, then the same listings in another (2026-10-27): the SKU prefix
// rule (the server's copy is pipeline/shop_listing.py), each shop's state, and the controls that
// say which shop they apply to.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";
import { editsShopText, profileIn, profileLocked, replacePrefix, shopLine, shopsShown, skuIn, stateIn } from "./anotherShop.ts";
import type { BatchShops } from "./types.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

const data: BatchShops = {
  shops: [
    { shop_id: "opa", name: "OPA", total: 3, not_sent: 0, drafted: 0, scheduled: 0, published: 3 },
    { shop_id: "pcs", name: "PCS", total: 3, not_sent: 2, drafted: 1, scheduled: 0, published: 0 },
    { shop_id: "ilc", name: "ILC", total: 3, not_sent: 3, drafted: 0, scheduled: 0, published: 0 },
  ],
  listings: [
    { content_id: "a", group_key: "PCS432", states: { opa: "published", pcs: "drafted" }, skus: { opa: "PCS432", pcs: "ILC432", ilc: "PCS432" }, profiles: { pcs: "p-pcs" } },
    { content_id: "b", group_key: "PCS435", states: { opa: "published" }, skus: { opa: "PCS435", pcs: "PCS435", ilc: "PCS435" }, profiles: {} },
    { content_id: "c", group_key: "NEW1", states: {}, skus: { opa: "NEW1", pcs: "NEW1", ilc: "NEW1" }, profiles: {} },
  ],
};

describe("whose text the review card edits (production, 2026-10-28)", () => {
  it("a shop's own only while the listing is on Etsy in another shop and not in this one", () => {
    assert.equal(editsShopText(data, "a", "ilc"), true); // in OPA and PCS, not in ILC
    assert.equal(editsShopText(data, "a", "pcs"), false); // already in PCS: edited on Etsy
    assert.equal(editsShopText(data, "c", "ilc"), false); // in no shop yet: the listing's own text
    assert.equal(editsShopText(data, "c", null), false);
    const page = readFileSync(join(root, "app/(app)/batches/[id]/review/page.tsx"), "utf8");
    assert.ok(page.includes("editsShopText(shopData, c.id, inView.id)"));
    assert.ok(page.includes("api.approveAll(id, inView?.id ?? null, only)"));
    const card = readFileSync(join(root, "components/ReviewCard.tsx"), "utf8");
    assert.ok(card.includes("api.approve(initial.id, on, textShop?.id ?? null)") && card.includes("setApproval(!approved)") && card.includes("card-approve-for-shop"));
  });
});

describe("replacing a SKU prefix (the server's pipeline/shop_listing.py::replace_prefix)", () => {
  it("replaces the leading prefix in any case and keeps the rest", () => {
    assert.deepEqual(replacePrefix("PCS432", "PCS", "ILC"), ["ILC432", true]);
    assert.deepEqual(replacePrefix("pcs1206", "PCS", "ILC"), ["ILC1206", true]);
    assert.deepEqual(replacePrefix(" PCS9 ", " PCS ", "ILC-"), ["ILC-9", true]);
  });
  it("keeps a SKU that does not start with it, and an empty prefix puts the new one in front", () => {
    assert.deepEqual(replacePrefix("XYZ7", "PCS", "ILC"), ["XYZ7", false]);
    assert.deepEqual(replacePrefix("AB1", "", "ILC"), ["ILCAB1", true]);
    assert.deepEqual(replacePrefix(null, "PCS", "ILC"), ["", false]);
  });
});

describe("a batch's listings per shop", () => {
  it("says each shop's state in a short line", () => {
    assert.equal(shopLine(data.shops[0]), "3 published");
    assert.equal(shopLine(data.shops[1]), "1 drafted · 2 not sent");
    assert.equal(shopLine(data.shops[2]), "not sent");
  });
  it("lists the shops with anything from the batch, and the shop in view", () => {
    assert.deepEqual(shopsShown(data, null).map((s) => s.shop_id), ["opa", "pcs"]);
    assert.deepEqual(shopsShown(data, "ilc").map((s) => s.shop_id), ["opa", "pcs", "ilc"]);
  });
  it("locks the profile only where the listing was sent (any shop when every shop is shown)", () => {
    assert.equal(profileLocked(data, "b", "opa"), true);
    assert.equal(profileLocked(data, "b", "pcs"), false); // on Etsy in OPA only: PCS's profile can be chosen
    assert.equal(profileLocked(data, "b", null), true);
    assert.equal(profileLocked(data, "c", null), false);
    assert.equal(stateIn(data, "a", "pcs"), "drafted");
    assert.equal(stateIn(data, "a", "ilc"), "not_sent");
  });
  it("shows the SKU and profile the listing has in the shop in view", () => {
    assert.equal(skuIn(data, "a", "pcs", "PCS432"), "ILC432");
    assert.equal(skuIn(data, "a", "opa", "PCS432"), "PCS432");
    assert.equal(skuIn(data, "a", null, "PCS432"), "PCS432");
    assert.equal(profileIn(data, "a", "pcs"), "p-pcs");
    assert.equal(profileIn(data, "a", "opa"), null);
  });
});

describe("the controls say where they apply", () => {
  const read = (path: string) => readFileSync(join(root, path), "utf8");
  it("the batch page offers Publish in another shop where the profile is locked, and names the shop in view", () => {
    const page = read("app/(app)/batches/[id]/page.tsx");
    assert.ok(page.includes("disabled={lockedKeys.has(g.key)}"));
    assert.ok(page.includes("Publish in another shop"));
    assert.ok(page.includes("api.setGroupSku(id, g.key, sku, inView)"));
    assert.ok(page.includes("onClick={toWorkspace}"));
  });
  it("Update SKU on Etsy names the shop it changes", () => {
    assert.ok(read("components/SkuEditor.tsx").includes("Update SKU on Etsy in ${where}"));
  });
  it("the review card locks the profile where the listing is on Etsy and links to the shop workspace", () => {
    const card = read("components/ReviewCard.tsx");
    assert.ok(card.includes("profileLocked(shopData, initial.id, inView?.id ?? null)"));
    assert.ok(card.includes("?select=${initial.id}"));
  });
});

// A send that stopped before Etsy (21 November 2026): the result says how many drafts were made and why the
// others were not, as soon as none is still being made; the shop's own line says the same.
it("the send's result: made, not made with the reason, waiting; settled once nothing is being made", async () => {
  const { sendResult, failedLine } = await import("./anotherShop.ts");
  const rows = [
    { content_id: "a", shop_state: "drafted", last_try: null },
    { content_id: "b", shop_state: "not_sent", last_try: { status: "failed" as const, reason: "BCT does not have the processing profile this listing used; set it up in BCT, then finish the draft" } },
    { content_id: "c", shop_state: "not_sent", last_try: { status: "working" as const, reason: null } },
    { content_id: "d", shop_state: "not_sent", last_try: { status: "waiting" as const, reason: "Preparing BCT's shipping, return and processing settings; the draft is made right after." } },
  ];
  assert.equal(sendResult(rows, ["a", "b", "c"], "BCT").settled, false);  // one is still being made
  const done = sendResult(rows, ["a", "b"], "BCT");
  assert.deepEqual([done.settled, done.made, done.failed], [true, 1, 1]);
  assert.equal(done.line, "1 of 2 drafts made in BCT. 1 not made: BCT does not have the processing profile this listing used; set it up in BCT, then finish the draft");
  assert.equal(sendResult(rows, ["a"], "BCT").line, "1 draft made in BCT.");
  const waits = sendResult(rows, ["a", "d"], "BCT");
  assert.equal(waits.settled, true);
  assert.equal(waits.line, "1 of 2 drafts made in BCT. 1 waiting: Preparing BCT's shipping, return and processing settings; the draft is made right after.");
  assert.equal(sendResult(rows, ["zz"], "BCT").settled, false);  // not read yet: still being made
  assert.equal(failedLine({ failed: 0 }), null);
  assert.equal(failedLine({ failed: 2, failure: "Etsy refused the copy." }), "2 drafts not made: Etsy refused the copy.");
  const page = (await import("node:fs")).readFileSync(new URL("../components/ShopWorkspace.tsx", import.meta.url), "utf8");
  for (const id of ["workspace-row-failed", "workspace-row-trying", "workspace-send-result", "shop-bar-failed"]) assert.ok(page.includes(id), id);
});
