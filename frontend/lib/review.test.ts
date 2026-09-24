// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyChange, cardKey, reviewActions } from "./review.ts";
import type { Content } from "./types.ts";

function item(id: string, over: Partial<Content> = {}): Content {
  return {
    id,
    approved: false,
    etsy_listing_id: null,
    etsy_listing_state: null,
    listing_link: null,
    ...over,
  } as Content;
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
  items = applyChange(items, { id: "a", etsy_listing_id: 42, etsy_listing_state: "draft" });
  assert.deepEqual(reviewActions(items), { approved: 2, toDraft: 1, toPublish: 1 });
});

test("live listings are not offered for publishing again", () => {
  const items = [item("a", { approved: true, etsy_listing_id: 1, etsy_listing_state: "active" })];
  assert.equal(reviewActions(items).toPublish, 0);
});

test("a card remounts when its Etsy state changes, not when it is approved", () => {
  const before = item("a");
  assert.equal(cardKey(before), cardKey({ ...before, approved: true }));
  assert.notEqual(cardKey(before), cardKey({ ...before, etsy_listing_id: 42, etsy_listing_state: "draft" }));
});
