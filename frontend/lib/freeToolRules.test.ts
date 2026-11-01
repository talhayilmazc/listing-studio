// The free tools answer what the app's rules answer: both read the same cases, written from the
// Python rules (backend/scripts/free_tool_cases.py, tests/test_free_tool_rules.py).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { spotBrands } from "./brandSpot.ts";
import { tagErrors, titleErrors } from "./etsyText.ts";
import { MAX_TITLE_WORDS } from "./listingRuleData.ts";
import { commerceIn, garmentsIn, giftIn, itemFirst, keywords, phraseCount, phrases, repeatedWords, sameSearchTags, subjectiveIn, tagsOnlyInTitle, wordCount } from "./listingRules.ts";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/free_tool_cases.json", import.meta.url), "utf8"));

for (const c of cases.titles) {
  test(`title: ${c.text}`, () => {
    assert.equal(!titleErrors(c.text).length, c.etsy_ok);
    assert.equal(wordCount(c.text), c.word_count);
    assert.deepEqual(phrases(c.text), c.phrases);
    assert.deepEqual(repeatedWords(c.text), c.repeated);
    assert.deepEqual(subjectiveIn(c.text), c.subjective);
    assert.deepEqual(commerceIn(c.text), c.commerce);
    assert.deepEqual(giftIn(c.text), c.gift);
    assert.deepEqual(garmentsIn(c.text), c.garments);
    assert.equal(itemFirst(c.text), c.report.item_first);
    assert.equal(wordCount(c.text) <= MAX_TITLE_WORDS, c.report.words);
    assert.deepEqual(!repeatedWords(c.text).length, c.report.repeats);
    assert.deepEqual([...spotBrands(c.text)].sort(), c.brands);
  });
}

for (const c of cases.tag_sets) {
  test(`tags: ${c.tags.slice(0, 3).join(", ")}`, () => {
    assert.equal(!tagErrors(c.tags).length, c.etsy_ok);
    assert.deepEqual(sameSearchTags(c.tags), c.same_search);
    assert.deepEqual(tagsOnlyInTitle(c.tags, c.title), c.only_title_words);
    assert.equal(phraseCount(c.tags), c.phrases);
    assert.deepEqual([...new Set(c.tags.flatMap((t: string) => spotBrands(t)))].sort(), c.brands);
  });
}

test("root words, as the app reads them", () => {
  assert.deepEqual(keywords("The nurses' T-Shirts for puppies"), ["nurse", "tshirt", "puppy"]);
});
