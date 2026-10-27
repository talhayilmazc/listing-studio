// A listing's own text in one shop (2026-10-27): the dialog's "Text for <shop>" step and its
// helpers for all listings, and the review card editing the picked shop's text only.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";
import { addTagsTo, addWordsTo, removeTagsFrom, removeWordsFrom, replaceIn, textFor } from "./anotherShop.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const read = (path: string) => readFileSync(join(root, path), "utf8");

describe("helpers for every listing at once", () => {
  it("adds tags not there yet (any case) and removes tags, keeping the rest in order", () => {
    assert.deepEqual(addTagsTo(["retro tee", "nurse gift"], "Nurse Gift, juniper tee , "), ["retro tee", "nurse gift", "juniper tee"]);
    assert.deepEqual(removeTagsFrom(["retro tee", "Nurse Gift", "x"], "nurse gift, x"), ["retro tee"]);
    // More than Etsy's 13 is kept: the rule check says so, nothing is dropped silently.
    assert.equal(addTagsTo(Array.from({ length: 13 }, (_, i) => `t${i}`), "one more").length, 14);
  });
  it("adds words to a title once, removes whole words only, and tidies commas and spaces", () => {
    assert.equal(addWordsTo("Retro Sunset Tee", "Juniper, tee"), "Retro Sunset Tee Juniper");
    assert.equal(removeWordsFrom("Retro Sunset Tee, Vintage Shirt", "vintage"), "Retro Sunset Tee, Shirt");
    assert.equal(removeWordsFrom("Teacher Tee, Tee Shirt", "tee"), "Teacher, Shirt");
    assert.equal(removeWordsFrom("Retro Teepee Tee", "tee"), "Retro Teepee");
  });
  it("finds and replaces in titles, any case, every match", () => {
    assert.equal(replaceIn("Northwind Tee by Northwind", "northwind", "Juniper"), "Juniper Tee by Juniper");
    assert.equal(replaceIn("Comfort Colors® Tee", "Comfort Colors® ", ""), "Tee");
    assert.equal(replaceIn("Same", "", "x"), "Same");
  });
});

describe("a listing's text in one shop", () => {
  const content = { title: "Listing", tags: ["a"], description: "Body", shop_texts: { pcs: { title: "PCS title" } } };
  it("takes the shop's own fields and the listing's for the rest", () => {
    assert.deepEqual(textFor(content, "pcs"), { title: "PCS title", tags: ["a"], description: "Body" });
    assert.deepEqual(textFor(content, "opa"), { title: "Listing", tags: ["a"], description: "Body" });
    assert.deepEqual(textFor(content, null), { title: "Listing", tags: ["a"], description: "Body" });
  });
});

describe("the workspace text editor and the card per shop", () => {
  it("each workspace row edits its text for the shop with the card's editors and checks, and says the rewrite rule first", () => {
    const editor = read("components/ShopTextEditor.tsx");
    for (const s of ["Keep as it is", "Rewrite for ${shopName}", "draftErrors(", "<TagEditor", "{rewriteRule}", "follows the listing"]) {
      assert.ok(editor.includes(s), s);
    }
  });
  it("the card edits the picked shop's text when the listing is not there yet, and says whose text it is", () => {
    const card = read("components/ReviewCard.tsx");
    assert.ok(card.includes("Editing text for "));
    assert.ok(card.includes("...(textShop ? { shop: textShop.id } : {})"));
    assert.ok(card.includes("Editing the listing&apos;s text: it goes to every shop without text of its own."));
    const page = read("app/(app)/batches/[id]/review/page.tsx");
    assert.ok(page.includes('stateIn(shopData, c.id, inView.id) === "not_sent"'));
  });
});
