// The production-partner note (A6, then fix/onboarding-followups Part H): said on the profile, and on a
// listing only inside its card's "Per shop" tab (at the card's foot when there is one shop), and only
// for a profile whose shop actually uses a partner (the server decides: creativity.production_note's
// shop_uses_partner). Never a list on the review page, never a large box.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("the production-partner note is on the profile, and in the card's Per shop tab", () => {
  assert.ok(read("components/ProfileCard.tsx").includes('data-testid="profile-production-note"'));
  const card = read("components/ReviewCard.tsx");
  assert.ok(card.includes('data-testid="card-production-note"'));
  assert.ok(card.includes('initial.production_note && (tab === "shops" || Object.keys(shopNames ?? {}).length <= 1)'));
  const page = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(!page.includes("production-notes") && !page.includes("production_note"));
});
