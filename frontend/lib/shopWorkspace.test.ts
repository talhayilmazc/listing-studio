// The batch page's shop workspace (seller feedback, 2026-10-27): the bar follows the sidebar and
// the URL, each row's profile, SKU and text are that shop's, rows already there are locked,
// bulk actions with previews, "Send N" with a summary, then that shop's Where and when.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";
import { selectable, sendSummary, shopColour, stateTone } from "./anotherShop.ts";
import type { AnotherShopPreview } from "./types.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const read = (path: string) => readFileSync(join(root, path), "utf8");

type Row = AnotherShopPreview["rows"][number];
const row = (over: Partial<Row>): Row => ({
  content_id: "x", group_key: "PCS10", title: "Retro Tee", sku_from: "PCS10", sku: "PCS10", state: "new", reason: null, note: null,
  tags: [], description: "", own_text: false, errors: [], warnings: [], free_rewrites_left: 3, profile_id: null,
  profile_name: "Standard Tee", sku_own: false, text_own: [], approved: true, shop_state: "not_sent", listing_link: null, ...over,
});

describe("a row belongs to the shop it was read for", () => {
  const source = read("components/ShopWorkspace.tsx");
  it("is a new row in another shop, so its open editor and waiting save stay with the old shop", () => {
    assert.match(source, /<WorkspaceRow key=\{`\$\{shopId\}:\$\{r\.content_id\}`\}/);
  });
  it("shows rows only under the shop they were read for, and a late answer for another shop is dropped", () => {
    assert.match(source, /const preview = loaded && loaded\.shopId === shopId \? loaded\.rows : null;/);
    assert.match(source, /if \(shopInView\.current === shopId\) setLoaded\(\{ shopId, rows \}\);/);
    assert.match(source, /if \(shopInView\.current === shopId\) setSummary\(answer\);/);
  });
});

describe("the send summary", () => {
  it("says the profiles used, the SKUs, the rows with their own text, and what is not sent with why", () => {
    const rows = [
      row({ content_id: "a", sku: "ILC10", sku_own: true, profile_name: "REGULAR - ILC", text_own: ["title"] }),
      row({ content_id: "b", sku: "PCS11" }),
      row({ content_id: "c", group_key: "PCS12", state: "cannot", reason: "not approved yet", approved: false }),
      row({ content_id: "d", group_key: "PCS13", errors: ["tag has characters Etsy does not allow"] }),
      row({ content_id: "e", group_key: "PCS14" }), // not selected
    ];
    const s = sendSummary({ rows, refused: [] }, ["a", "b", "c", "d"]);
    assert.equal(s.going, 2);
    assert.deepEqual(s.profiles, [["REGULAR - ILC", 1], ["Standard Tee", 1]]);
    assert.deepEqual(s.skus, ["ILC10", "PCS11"]);
    assert.equal(s.ownText, 1);
    assert.equal(s.ownSku, 1);
    assert.deepEqual(s.notGoing, ["PCS12: not approved yet", "PCS13: Etsy would refuse its text (tag has characters Etsy does not allow)"]);
  });
  it("offers to approve, in one click, the rows stopped only by approval for this shop (never a dead end)", () => {
    const rows = [
      row({ content_id: "a", state: "cannot", reason: "Edited for ILC: approve again", approved: false }),
      row({ content_id: "b", state: "cannot", reason: "not approved yet", approved: false }),
      row({ content_id: "c", state: "cannot", reason: "not approved yet", approved: false, errors: ["title is empty"] }),
      row({ content_id: "d" }),
    ];
    const s = sendSummary({ rows, refused: [] }, ["a", "b", "c", "d"]);
    assert.deepEqual(s.toApprove, ["a", "b"]); // the one Etsy would refuse is fixed first, not approved
    assert.equal(s.going, 1);
    assert.equal(s.notGoing.length, 3);
    const ws = read("components/ShopWorkspace.tsx");
    for (const t of ["Approve these ${s.toApprove.length} for ${shop.name}", "Approve them for ${shop.name} first",
      "Nothing here can go yet: see why above"]) assert.ok(ws.includes(t), t);
  });
  it("lists SKUs the shop already uses", () => {
    assert.deepEqual(sendSummary({ rows: [row({})], refused: ["X1 (PCS10) is already used in ILC by listing 5."] }, ["x"]).notGoing,
      ["X1 (PCS10) is already used in ILC by listing 5."]);
  });
});

describe("rows and the bar", () => {
  it("rows already in the shop cannot be selected; each state has its tone; each shop its colour", () => {
    assert.equal(selectable({ state: "there" }), false);
    assert.equal(selectable({ state: "new" }), true);
    assert.equal(selectable({ state: "cannot" }), true);
    assert.equal(stateTone("published"), "done");
    assert.equal(stateTone("not_sent"), "neutral");
    assert.equal(shopColour(0), shopColour(6));
    assert.notEqual(shopColour(0), shopColour(1));
  });
  it("the bar follows the sidebar's shop and the URL, both ways", () => {
    const page = read("app/(app)/batches/[id]/page.tsx");
    assert.ok(page.includes('const wanted = search?.get("shop")'));
    assert.ok(page.includes("select(wanted)"));
    assert.ok(page.includes('url.searchParams.set("shop", inView)'));
    assert.ok(page.includes("onPick={select}"));
    assert.ok(page.includes("shopId={inView}"));
  });
  it("one way to send elsewhere: the old dialog is gone and every offer brings the workspace up", () => {
    const page = read("app/(app)/batches/[id]/page.tsx");
    assert.ok(!page.includes("AnotherShopDialog"));
    assert.ok(page.includes("onClick={toWorkspace}"));
    assert.ok(read("components/ReviewCard.tsx").includes("?select=${initial.id}"));
  });
});

describe("the workspace", () => {
  const ws = read("components/ShopWorkspace.tsx");
  it("each row saves its profile, SKU and text for the shop at once, and says what follows the listing", () => {
    for (const s of ["api.workspaceProfile(batchId, shop.id, [row.content_id]", "api.workspaceSkus(batchId, shop.id, { [row.content_id]: sku })",
      "api.workspaceSkusCheck(batchId, shop.id, { [row.content_id]: sku })", "api.workspaceTexts(batchId, shop.id", "Follows the listing",
      "Text follows the listing.", "Listing's own ("]) {
      assert.ok(ws.includes(s), s);
    }
  });
  it("rows already in the shop are locked: a link to Etsy, edited on Etsy, and Update SKU on Etsy as today", () => {
    assert.ok(ws.includes("disabled={locked}"));
    assert.ok(ws.includes("View on Etsy"));
    assert.ok(ws.includes("Edited on Etsy"));
    assert.ok(ws.includes("<SkuEditor compact"));
  });
  it("bulk actions, select helpers, approve selected and Send N; the bulk bar is a bottom sheet on a phone", () => {
    for (const s of ["Replace SKU prefix", "bulk-sku-preview", "bulk-sku-refused", "Add these tags", "Remove these words from titles",
      "Find in titles", "Select every row not yet in ${shop.name}", "Select none", "Approve selected (", "Send ${approvable} to ${shop.name}",
      "fixed inset-x-0 bottom-0", "sm:static"]) {
      assert.ok(ws.includes(s), s);
    }
  });
  it("the summary comes first, then the drafts, then that shop's Where and when for the rows sent", () => {
    assert.ok(ws.includes("api.workspaceRows(batchId, shopId, chosen)")); // a read, never POST another-shop/preview
    assert.ok(!ws.includes("anotherShopPreview"));
    assert.ok(ws.includes("api.anotherShop(batchId, { connection_id: shopId, content_ids: ids"));
    assert.ok(ws.includes("api.sendPreview(batchId, when, when === \"spread\" ? schedule : null, only)"));
    assert.ok(ws.includes("Keep as drafts"));
  });
});
