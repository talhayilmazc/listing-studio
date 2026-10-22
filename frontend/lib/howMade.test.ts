// How it's made (Etsy's Creativity Standards): the picker's rules, the one line under it, and no
// per-listing step anywhere (the owner's rule, 2026-10-23: the seller does nothing per listing).
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { NOT_SENT, cardLine, problem, shortWords, toggleTool } from "./howMade.ts";
import { steps } from "./firstRun.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const exists = (path: string) => existsSync(new URL(`../${path}`, import.meta.url));

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

test("one short line under the selector says Etsy can't take it yet and where Shop Manager sets it", () => {
  assert.equal(NOT_SENT,
    "Etsy doesn't let apps set this yet, so it can't be sent automatically. You can set it for many listings at once in Shop Manager → Listings → select → Editing options.");
  assert.ok(read("components/HowMadePicker.tsx").includes("{NOT_SENT}"));
  assert.ok(read("components/HowMadeBulk.tsx").includes("{NOT_SENT}"));
  assert.ok(read("components/ProfileCard.tsx").includes("<HowMadePicker"));
  assert.ok(read("components/ProfileShopTable.tsx").includes("<HowMadeBulk"));
});

test("nothing per listing: no tick list, no page, no badge, no line on cards; the profile step needs no answers", () => {
  assert.ok(!exists("app/(app)/batches/finish/page.tsx"), "no Finish in Etsy page");
  for (const file of ["components/Rail.tsx", "components/ReviewCard.tsx", "components/WhereAndWhen.tsx", "components/TopStrip.tsx",
    "app/(app)/batches/[id]/review/page.tsx", "lib/api.ts", "components/HowMadePicker.tsx", "components/HowMadeBulk.tsx"]) {
    const src = read(file);
    assert.ok(!src.includes("/batches/finish") && !src.includes("Finish in Etsy"), file);
    assert.ok(!/tick (the same|in Etsy)/i.test(src), `${file}: no "tick in Etsy" instruction`);
  }
  assert.ok(!read("components/ReviewCard.tsx").includes("how_made"), "no How it's made line on a listing card");
  const facts = { shops: 1, confirmedProfiles: 1, batches: 0, sent: 0 };
  assert.equal(steps(facts)[1].done, true, "a confirmed profile is a finished step, answers or not");
  assert.ok(!read("components/FirstRunGuide.tsx").includes("how_made"));
});
