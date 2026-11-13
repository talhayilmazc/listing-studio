// The review page's bar: a plain sentence and one primary action (Part H).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { primaryAction, summaryLine } from "./reviewBar.ts";

test("the sentence says what will happen, in plain words", () => {
  assert.equal(summaryLine({ listings: 2, shops: 4, when: "drafts" }, 2, 2),
    "2 approved listings will go to 4 shops as drafts. 2 more are waiting for your approval.");
  assert.equal(summaryLine({ listings: 1, shops: 1, when: "publish" }, 1, 0), "1 approved listing will go to 1 shop and go live now.");
  assert.equal(summaryLine({ listings: 0, shops: 0, when: "drafts" }, 3, 0), "Every approved listing is already in its shops.");
  assert.equal(summaryLine({ listings: 0, shops: 0, when: "drafts" }, 0, 3), "Approve listings to send them. 3 listings are waiting for your approval.");
  assert.equal(summaryLine(null, 0, 1), "Approve listings to send them. 1 listing is waiting for your approval.");
});

test("one primary action at a time: approve selected, then send, then approve", () => {
  assert.deepEqual(primaryAction({ selectedUnapproved: 2, toSend: 4, unapproved: 3, example: false }),
    { kind: "approve-selected", count: 2, label: "Approve selected (2)" });
  assert.deepEqual(primaryAction({ selectedUnapproved: 0, toSend: 4, unapproved: 3, example: false }),
    { kind: "send", count: 4, label: "Send (4)" });
  assert.deepEqual(primaryAction({ selectedUnapproved: 0, toSend: 0, unapproved: 3, example: false }), { kind: "approve", label: "Approve" });
  // The examples never go to Etsy: no Send.
  assert.deepEqual(primaryAction({ selectedUnapproved: 0, toSend: 2, unapproved: 1, example: true }), { kind: "approve", label: "Approve" });
  assert.equal(primaryAction({ selectedUnapproved: 0, toSend: 0, unapproved: 0, example: false }), null);
});

test("the page: Approve all and Publish all drafts are in More; no Select listings card; notes in Per shop", () => {
  const page = readFileSync(new URL("../app/(app)/batches/[id]/review/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes('data-testid="review-more"'));
  assert.ok(!page.includes("Select listings to approve several at once."));
  assert.ok(!page.includes("production-notes"));
  const bulk = readFileSync(new URL("../components/BulkItemOptions.tsx", import.meta.url), "utf8");
  assert.ok(!bulk.includes('"Select listings"'));
  const card = readFileSync(new URL("../components/ReviewCard.tsx", import.meta.url), "utf8");
  assert.ok(card.includes('data-testid="card-production-note"'));
});
