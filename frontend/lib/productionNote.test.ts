// A6: "No production partner on this profile" is said once on the profile and as one short line
// per profile on the review page, linking to it; never as a large box on every listing card.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("the production-partner note is on the profile, and one line per profile on the review page", () => {
  assert.ok(!read("components/ReviewCard.tsx").includes("production_note"), "not on every listing card");
  assert.ok(read("components/ProfileCard.tsx").includes('data-testid="profile-production-note"'));
  const page = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(page.includes('data-testid="production-notes"') && page.includes("/profiles#profile-${p.id}"));
});
