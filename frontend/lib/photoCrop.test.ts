// "Crop" on every photo (feature/photo-crop): the existing cover cropper, reachable from each photo
// screen, biggest on the cover. Nothing new is cropped on the server: the square is the photo's
// cover_crop, used when it is a listing's cover (a new draft, Replace images).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("one Crop button opens the existing cropper; the cover's is the big one", () => {
  const button = read("components/CropButton.tsx");
  assert.ok(button.includes("<CoverCropper"));
  assert.ok(button.includes('cover ? "btn-secondary px-3 py-1.5 text-sm "'));
  assert.ok(button.includes('cover ? "Crop cover" : "Crop"'));
  assert.ok(!button.includes("onMouseEnter") && !button.includes("group-hover"), "visible, never hover only");
});

test("every photo screen has it: the batch page strip, the review card, the per-shop photos", () => {
  const strip = read("components/GroupImages.tsx");
  assert.ok(strip.includes("<CropButton assetId={id} batchId={batchId} cover={false}"));
  assert.ok(strip.includes('data-testid="crop-cover"') && strip.includes('"Crop cover"'));
  assert.ok(read("components/ReviewCard.tsx").includes("<CropButton assetId={current} batchId={batchId} cover={isCover} />"));
  assert.ok(read("components/ShopPhotoEditor.tsx").includes("<CropButton key=\"crop\" assetId={id} batchId={batchId} cover={n === 0}"));
});
