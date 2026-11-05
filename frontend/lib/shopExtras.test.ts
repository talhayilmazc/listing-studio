// Per-shop photos and the attribute editor (customer requests, 2026-11-03 and 2026-11-05): the editor's moves, Etsy's
// limit said, the cards showing a shop's own cover, the send summary's counts, and every write behind a click.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { sendSummary } from "./anotherShop.ts";
import { canFollow, goodNumber, setCount, shown, valuesFor, withValue } from "./listingAttributes.ts";
import { dropBefore, leaveOut, limitLine, makeCover, nudge, putBack, shopCover } from "./shopPhotos.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("the photo moves: earlier, later, a drag, the cover, left out and put back", () => {
  assert.deepEqual(nudge(["a", "b", "c"], "b", -1), ["b", "a", "c"]);
  assert.deepEqual(nudge(["a", "b", "c"], "c", 1), ["a", "b", "c"]);
  assert.deepEqual(dropBefore(["a", "b", "c"], "c", "a"), ["c", "a", "b"]);
  assert.deepEqual(makeCover(["a", "b", "c"], "c"), ["c", "a", "b"]);
  assert.deepEqual(leaveOut(["a", "b"], [], "a"), { order: ["b"], out: ["a"] });
  assert.deepEqual(leaveOut(["a"], ["b"], "a"), { order: ["a"], out: ["b"] }); // the last photo stays: a listing needs a cover
  assert.deepEqual(putBack(["b"], ["a"], "a"), { order: ["b", "a"], out: [] });
});

test("Etsy's limit is said with the size charts", () => {
  assert.equal(limitLine(3, 20, 0), "3 of 20 photos. Size charts keep their places.");
  assert.equal(limitLine(19, 18, 2), "Too many: Etsy keeps 20 images, so 18 photos fit beside its 2 size charts. Leave 1 more out.");
});

test("the cards show a shop's own cover only when that shop has one and is picked", () => {
  const listings = [{ content_id: "c", photos: { b: ["p3", "p1"] } }];
  assert.equal(shopCover(listings, "c", "b"), "p3");
  assert.equal(shopCover(listings, "c", "a"), null);
  assert.equal(shopCover(listings, "c", null), null);
  assert.ok(read("components/ReviewCard.tsx").includes("shopCover(shopData?.listings, initial.id, inView?.id)"));
});

test("the send summary counts own photo orders and own attributes, as it does text and SKUs", () => {
  const row = (over: object) => ({ content_id: "a", state: "new", errors: [], warnings: [], approved: true, sku: "A", sku_own: false, text_own: [],
    title: "T", group_key: "A", profile_name: "P", photos_own: false, attributes_own: false, ...over });
  const s = sendSummary({ rows: [row({ content_id: "a", photos_own: true }), row({ content_id: "b", attributes_own: true })], refused: [] } as never, ["a", "b"]);
  assert.deepEqual([s.ownPhotos, s.ownAttributes], [1, 1]);
  assert.ok(read("components/ShopWorkspace.tsx").includes("with their own photo order for ${shop.name}"));
});

test("attributes: what is shown, values by unit, Etsy's limit, numbers", () => {
  assert.equal(shown({ current: [], unit: null }), "Not set");
  assert.equal(shown({ current: ["12"], unit: "Inches" }), "12 Inches");
  assert.deepEqual(valuesFor({ values: ["S", "36"], value_units: { S: "US", "36": "EU" } }, "EU"), ["36"]);
  assert.deepEqual(valuesFor({ values: ["S", "36"], value_units: {} }, null), ["S", "36"]);
  assert.deepEqual(withValue(["Christmas"], "Easter", 2), ["Christmas", "Easter"]);
  assert.deepEqual(withValue(["Christmas", "Easter"], "Halloween", 2), ["Christmas", "Easter"]);
  assert.deepEqual(withValue(["Birthday"], "Wedding", 1), ["Wedding"]);
  assert.ok(goodNumber("12.5") && !goodNumber("0") && !goodNumber("wide"));
  assert.equal(setCount([{ current: ["a"] }, { current: [] }] as never), 1);
  assert.ok(canFollow({ source: "seller" }, false) && !canFollow({ source: "writer" }, false) && canFollow({ source: "shop" }, true));
});

test("nothing is written without a click: the editors save on a button or a choice, bulk changes after a preview", () => {
  const photos = read("components/ShopPhotoEditor.tsx");
  assert.equal(photos.match(/api\.setShopPhotos\(/g)?.length, 1);
  assert.ok(photos.includes('data-testid="shop-photo-save"') && photos.includes("Keep as it is"));
  const bulk = read("components/BulkExtras.tsx");
  assert.ok(bulk.includes("apply: false") && bulk.includes("apply: true") && bulk.includes("Preview"));
  assert.ok(read("components/AttributesEditor.tsx").includes("Keep as it is"));
  // Occasion and Holiday are edited in one place: the Attributes section.
  assert.ok(read("components/ReviewCard.tsx").includes("properties={false}"));
});
