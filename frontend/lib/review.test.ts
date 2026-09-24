// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyChange, cardKey, pendingManualSteps, reviewActions } from "./review.ts";
import type { Content, Publication } from "./types.ts";

function item(id: string, over: Partial<Content> = {}): Content {
  return { id, approved: false, connection_id: "shop-1", publications: [], ...over } as Content;
}

const STEP = { key: "creativity_production", label: "How does your shop produce this item?", detail: "d" };

function pub(shop: string, state = "draft", listing = 1): Publication {
  const manual_steps = state === "active" ? [] : [STEP];
  return { connection_id: shop, shop_name: shop, etsy_listing_id: listing, state, listing_link: "x", manual_steps };
}

test("nothing approved: neither bulk action has work", () => {
  assert.deepEqual(reviewActions([item("a"), item("b")]), { approved: 0, toDraft: 0, toPublish: 0 });
});

test("approving in a card makes 'Create drafts for all' available without a reload", () => {
  let items = [item("a"), item("b")];
  items = applyChange(items, { id: "a", approved: true });
  assert.equal(reviewActions(items).toDraft, 1);
});

test("a finished draft job makes 'Publish all' available without a reload", () => {
  let items = [item("a", { approved: true }), item("b", { approved: true })];
  assert.equal(reviewActions(items).toPublish, 0);
  items = applyChange(items, { id: "a", publications: [pub("shop-1")] });
  assert.deepEqual(reviewActions(items), { approved: 2, toDraft: 1, toPublish: 1 });
});

test("live listings are not offered for publishing again", () => {
  const items = [item("a", { approved: true, publications: [pub("shop-1", "active")] })];
  assert.equal(reviewActions(items).toPublish, 0);
});

test("with two target shops, a draft in one still leaves work in the other", () => {
  const items = [item("a", { approved: true, publications: [pub("shop-1")] })];
  assert.equal(reviewActions(items).toDraft, 0); // its own shop is done
  assert.equal(reviewActions(items, ["shop-1", "shop-2"]).toDraft, 1);
  assert.equal(reviewActions(items, ["shop-1"]).toDraft, 0);
});

test("a card remounts when a draft changes, not when it is approved", () => {
  const before = item("a");
  assert.equal(cardKey(before), cardKey({ ...before, approved: true }));
  assert.notEqual(cardKey(before), cardKey({ ...before, publications: [pub("shop-2", "draft", 42)] }));
  assert.notEqual(
    cardKey({ ...before, publications: [pub("shop-2", "draft", 42)] }),
    cardKey({ ...before, publications: [pub("shop-2", "active", 42)] }),
  );
});

test("drafts still needing a Shop Manager setting are counted before Publish all", () => {
  const items = [
    item("a", { approved: true, publications: [pub("shop-1"), pub("shop-2")] }),
    item("b", { approved: true, publications: [pub("shop-1", "active")] }), // live: nothing left
    item("c", { approved: false, publications: [pub("shop-1")] }), // not approved: not offered
  ];
  assert.deepEqual(pendingManualSteps(items), [{ ...STEP, drafts: 2 }]);
  assert.deepEqual(pendingManualSteps([item("d")]), []);
});
