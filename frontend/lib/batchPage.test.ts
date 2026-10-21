// B3: the batch page asks no shop; one top row; photos arranged in place; profile names, not shops.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("no batch shop and no group shop on the batch page", () => {
  const page = read("app/(app)/batches/[id]/page.tsx");
  for (const gone of ["Shop for this batch", "ShopPicker", "setBatchShop", "connection_id: shop", "GroupingBoard", "Arrange photos", "profile-setup"]) {
    assert.ok(!page.includes(gone), gone);
  }
  assert.ok(page.includes('data-testid="batch-top-row"') && page.includes("Generate all") && page.includes("Write them myself"));
});

test("photos are arranged in place: drop onto a card, + per card, + New listing, unsorted tiles", () => {
  const page = read("app/(app)/batches/[id]/page.tsx");
  for (const piece of ["{...dropTarget(g.key)}", 'data-testid="add-to-group"', 'data-testid="batch-new-listing"', 'data-testid="unsorted-tray"',
                       "api.boardMove(id, assetIds, newListingName(), true)"]) {
    assert.ok(page.includes(piece), piece);
  }
  assert.ok(read("components/GroupImages.tsx").includes("e.dataTransfer.setData(ASSET_DRAG"), "a strip's photo can leave its card");
});

test("profile names, not shops, on review cards and batch cards", () => {
  assert.ok(!read("components/ReviewCard.tsx").includes("Written for"));
  assert.ok(read("components/ReviewCard.tsx").includes('data-testid="card-profile-name"'));
  assert.ok(!read("app/(app)/batches/page.tsx").includes("<ShopBadge"));
});
