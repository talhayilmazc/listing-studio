// How it's made (Etsy's Creativity Standards): the picker's rules, the lines, the page's helpers.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { cardLine, nextRow, problem, shortWords, toggleTool, topLine } from "./howMade.ts";
import { steps } from "./firstRun.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test('"none" stands alone: ticking it clears the others, ticking another clears it', () => {
  assert.deepEqual(toggleTool(["handheld", "computerized"], "none", true), ["none"]);
  assert.deepEqual(toggleTool(["none"], "computerized", true), ["computerized"]);
  assert.deepEqual(toggleTool(["computerized"], "handheld", true), ["handheld", "computerized"]);
  assert.deepEqual(toggleTool(["handheld", "computerized"], "handheld", false), ["computerized"]);
});

test("the picker says what is missing, the same rules as the server", () => {
  assert.equal(problem(null, ["computerized"]), "Choose how your shop produces this item.");
  assert.equal(problem("altered", []), "Choose the tools used, or None.");
  assert.match(problem("altered", ["none", "ai"]) ?? "", /cannot be chosen with other tools/);
  assert.equal(problem("altered", ["computerized"]), null);
  assert.equal(problem("altered", ["none"]), null);
});

test("the card's line, set and not set", () => {
  assert.equal(cardLine("altered", ["computerized"]), "How it's made: shop alters it · computerized tools");
  assert.equal(cardLine(null, null), "How it's made: not set");
  assert.equal(shortWords("made_from_scratch", ["handheld", "ai"]), "made from scratch · handheld tools, AI generator");
});

test("the page's top line tells drafts and active listings apart", () => {
  assert.equal(topLine(3, 0), "3 drafts: open, tick How it's made, press Publish.");
  assert.equal(topLine(1, 2), "1 draft: open, tick How it's made, press Publish. 2 active listings: open, tick How it's made, save.");
  assert.equal(topLine(0, 0), "Nothing to finish in Etsy.");
});

test('"Open next" walks the group one by one', () => {
  const rows = [{ key: "a" }, { key: "b" }, { key: "c" }];
  assert.equal(nextRow(rows, new Set(), new Set())?.key, "a");
  assert.equal(nextRow(rows, new Set(["a"]), new Set())?.key, "b");
  assert.equal(nextRow(rows, new Set(["a"]), new Set(["b"]))?.key, "c");
  assert.equal(nextRow(rows, new Set(["a", "b", "c"]), new Set()), null);
});

test("a confirmed profile without How it's made is an unfinished first-run step", () => {
  const facts = { shops: 1, confirmedProfiles: 2, batches: 0, sent: 0 };
  assert.equal(steps({ ...facts, profilesWithoutHowMade: 1 })[1].done, false);
  assert.match(steps({ ...facts, profilesWithoutHowMade: 1 })[1].body, /Set How it's made on 1 profile/);
  assert.equal(steps({ ...facts, profilesWithoutHowMade: 0 })[1].done, true);
});

test("the page, the review card, Where and when and the sidebar", () => {
  const page = read("app/(app)/batches/finish/page.tsx");
  for (const word of ["Open in Etsy", "Open next", "Mark done", "Done", "editor_link", "finishInEtsyOpened", "went_live"]) {
    assert.ok(page.includes(word), word);
  }
  assert.ok(!/window\.open\([^)]*etsy\.com/.test(page), "links only; the page never drives Etsy's site");
  assert.ok(read("components/ReviewCard.tsx").includes("how_made_line"));
  assert.ok(read("components/ReviewCard.tsx").includes('href="/batches/finish"'));
  assert.ok(read("components/WhereAndWhen.tsx").includes("preview.how_made"));
  assert.ok(read("components/Rail.tsx").includes('{ href: "/batches/finish", label: "Finish in Etsy"'));
  assert.ok(!read("components/ReviewCard.tsx").includes("Recommended in Shop Manager"), "the generic step text is gone");
  assert.ok(read("components/ProfileShopTable.tsx").includes("<HowMadeBulk"));
  assert.ok(read("components/ProfileCard.tsx").includes("<HowMadePicker"));
});
