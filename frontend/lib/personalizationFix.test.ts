// Personalization never stops a publish silently (2026-10-25): the profile field's counter and
// rules, the caps fix (the same cases as the server), the offer applied only on "Use this", the
// held card, and the Scheduled page's "missed" and "held".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { counter, fixCaps, personalizationErrors, shouted } from "./personalization.ts";
import { STATUS_LABELS } from "./scheduled.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const read = (path: string) => readFileSync(join(root, path), "utf8");

test("the caps fix keeps the first word in capitals, as the server does", () => {
  const cases = JSON.parse(readFileSync(join(root, "..", "backend", "tests", "fixtures", "personalization_caps_cases.json"), "utf8"));
  for (const c of cases) assert.equal(fixCaps(c.text), c.fixed, c.text);
  assert.deepEqual(shouted("PLEASE ENTER the NAME"), ["PLEASE", "ENTER", "NAME"]);
});

test("the live counter says the length against Etsy's limit", () => {
  assert.deepEqual(counter("x".repeat(131), 120), { line: "131 / 120 characters", over: true });
  assert.deepEqual(counter("  Type the name ", 120), { line: "13 / 120 characters", over: false });
  assert.deepEqual(counter(null, 45), { line: "0 / 45 characters", over: false });
});

test("the profile field counts as you type, lists Etsy's rules, and never saves text Etsy would refuse", () => {
  const src = read("components/PersonalizationEditor.tsx");
  assert.ok(!/maxLength=/.test(src), "no silent cut: a paste over the limit is shown, not truncated");
  assert.match(src, /data-testid="profile-instructions-count"/);
  assert.match(src, /data-testid="profile-personalization-rules"/);
  assert.match(src, /disabled=\{busy \|\| problems\.length > 0\}/);
  assert.match(src, /if \(problems\.length\) return; \/\/ Etsy would refuse it: never saved/);
  assert.ok(personalizationErrors({ enabled: true, question_text: "Name", instructions: "x".repeat(121), required: false, max_allowed_characters: null }).length);
  assert.ok(personalizationErrors({ enabled: true, question_text: "Name", instructions: "PLEASE ENTER THE NAME", required: false, max_allowed_characters: null }).length);
  // The card's own editor counts too, without cutting.
  const card = read("components/ListingPersonalization.tsx");
  assert.ok(!/maxLength=/.test(card) && card.includes("counter(draft.instructions, MAX_INSTRUCTIONS)"));
});

test("the fix is an offer beside the original, applied only on Use this", () => {
  const src = read("components/PersonalizationFix.tsx");
  assert.match(src, /api\.shortenPersonalization\(field\.field, field\.caps_fix \?\? field\.text\)/);
  // The only thing that changes anything is the Use this button.
  assert.equal((src.match(/onUse\(/g) ?? []).length, 2, "one call per field, from Use this");
  assert.match(src, /data-testid="personalization-use"/);
  assert.ok(src.includes("now") && src.includes("Shorter, same meaning") && src.includes("Only the first word in capitals"));
  // On a profile and on any held listing card.
  assert.match(read("components/PersonalizationEditor.tsx"), /api\.fixProfilePersonalization\(profile\.id, body\)/);
  const card = read("components/ReviewCard.tsx");
  assert.match(card, /\{hold && \(\s*<PersonalizationFix/);
  assert.match(card, /api\.fixContentPersonalization\(initial\.id, body\)/);
});

test("the Scheduled page says missed and held, never waiting", () => {
  assert.equal(STATUS_LABELS.missed, "Missed");
  assert.equal(STATUS_LABELS.held, "Held: fix personalization");
  const page = read("app/(app)/scheduled/page.tsx");
  assert.match(page, /missed: "attention"/);
  assert.match(page, /held: "attention"/);
});
