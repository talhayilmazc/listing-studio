// B5: personalization comes from each listing's profile; one line per card, "Personalization…" for
// selected listings, no batch-wide section.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { personalizationLine } from "./personalization.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const on = { enabled: true, question_text: "Name", instructions: "", required: false, max_allowed_characters: null };

test("one line says where it comes from and whether it is on", () => {
  assert.equal(personalizationLine(on, "profile"), "Personalization: from its profile (on)");
  assert.equal(personalizationLine({ ...on, required: true }, "listing"), "Personalization: this listing's own (on, required)");
  assert.equal(personalizationLine({ ...on, enabled: false }, "profile"), "Personalization: from its profile (off)");
  assert.equal(personalizationLine(null, "unknown"), "Personalization: from its profile (read when its reference is read)");
});

test("the card shows the line and opens the editor on change; no batch-wide section", () => {
  const card = read("components/ReviewCard.tsx");
  assert.ok(card.includes('data-testid="card-personalization"') && card.includes("personalizationLine(personalization, personalizationSource)"));
  assert.ok(card.includes("{editingPersonalization && ("), "the editor opens only on change");
  const page = read("app/(app)/batches/[id]/review/page.tsx");
  for (const gone of ["Personalization: set for all listings", "personalize-all", "Every listing follows its profile again"]) {
    assert.ok(!page.includes(gone), gone);
  }
  const bulk = read("components/BulkItemOptions.tsx");
  assert.ok(bulk.includes("Personalization…") && bulk.includes("api.setPersonalizationForAll(batchId, setting, selected)"));
  assert.ok(bulk.includes("Follow their profiles"));
});
