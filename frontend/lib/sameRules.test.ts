// One rule set for every listing, whoever wrote it (2026-10-26): text a seller typed, or an AI
// listing they rewrote by hand, is stopped only by what Etsy refuses; the app's quality rules are
// notes on every card. Etsy's refusals say the fix in plain words.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { draftErrors, tagErrors, titleErrors } from "./etsyText.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const card = readFileSync(join(root, "components", "ReviewCard.tsx"), "utf8");

test("the card judges every listing by Etsy's own rules, and shows the notes on every card", () => {
  assert.match(card, /const errors = useMemo\(\(\) => draftErrors\(title, tags, description\), \[title, tags, description\]\);/);
  assert.ok(!/manual \? draftErrors/.test(card), "no separate, stricter check for listings the app wrote");
  assert.ok(!/function validate\(/.test(card));
  assert.match(card, /\{notes\.length > 0 && \(/);
});

test("a short title with five tags is not a stop for anyone", () => {
  assert.deepEqual(draftErrors("Custom Name Teacher Tee", ["teacher", "name", "custom", "tee", "school"], "Body"), []);
});

test("Etsy's refusals say what to do", () => {
  assert.match(tagErrors(["mom’s gift"])[0], /use a straight apostrophe \('\) instead of a curly one/);
  assert.match(tagErrors(["tee – 2026"])[0], /use a hyphen \(-\) instead of a dash/);
  assert.match(tagErrors(["mom & dad"])[0], /write "and" instead of &/);
  assert.match(titleErrors("Love Tee ❤")[0], /remove them \(emoji, symbols, tabs\)/);
  assert.match(titleErrors("Mom & Dad & Me Tee")[0], /write "and" for the other &/);
  assert.deepEqual(titleErrors("Mom’s Tee"), []); // a curly apostrophe is punctuation: fine in a title
});
