// The review and send flow is the 13 November (31fa2f5) design; on its card: why a listing was not approved,
// "Keep it, I know the risk" for a brand name, and a section choice that sticks.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { fixFor, stoppedByBrand } from "./notApproved.ts";
import { pickOf, sectionLabel, SECTION_NOT_SAVED } from "./sections.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the review page is Friday's: Approve all and Where and when on the page, no bar, no More menu", () => {
  const page = read("../app/(app)/batches/[id]/review/page.tsx");
  for (const gone of ['data-testid="review-bar"', 'data-testid="review-more"', "primaryAction", "@/lib/reviewBar"]) {
    assert.ok(!page.includes(gone), `${gone} is not part of the 13 November review page`);
  }
  assert.ok(page.includes("<WhereAndWhen"), "Where and when is on the page");
  assert.ok(page.includes("Approve all"), "Approve all is on the page");
  assert.ok(!existsSync(new URL("./reviewBar.ts", import.meta.url)), "the bar's helpers are gone with the bar");
});

test("a listing that cannot be approved says why on its card, with the one action that fixes it", () => {
  assert.deepEqual(fixFor("Remove: Mickey (title)"), { field: "title", label: "Fix the title" });
  assert.deepEqual(fixFor("Remove: mickey mouse tee (tag)"), { field: "tags", label: "Fix the tags" });
  assert.deepEqual(fixFor("title is empty"), { field: "title", label: "Fix the title" });
  assert.equal(fixFor("compliance: a character in the drawing"), null);
  const card = read("../components/ReviewCard.tsx");
  assert.ok(card.includes('data-testid="approve-toggle"') && !card.includes("approve-top"), "the card's own Approve, where it was on Friday");
  assert.ok(card.includes('testId="card-blocked"') && card.includes("setRefused("), "a refusal is said on the card");
  assert.ok(!card.includes("disabled={!valid && !approved}"), "the card's Approve is never a dead button: pressed, it says why");
});

test("Keep it, I know the risk: beside the reason, only when a brand name is what stops the listing", () => {
  assert.ok(stoppedByBrand("Remove: Mickey (title)"));
  assert.ok(stoppedByBrand("title is longer than 140 characters; Remove: Disney, Mickey (title)"));
  assert.ok(!stoppedByBrand("title is empty"));
  assert.ok(!stoppedByBrand("compliance: The artwork shows Mickey Mouse."));
  const card = read("../components/ReviewCard.tsx");
  assert.ok(card.includes("{stoppedByBrand(why) ? (") && card.includes("{KEEP_LABEL}"), "the same words as Listing health");
  assert.ok(card.includes("api.keepBrands(initial.id"), "one click: recorded on the server, then approved");
  assert.ok(card.includes('data-testid="card-kept-brand"') && card.includes("Stop keeping it"), "the decision is said, with a way back");
  // The warning box is drawn whatever is kept: keeping never hides it.
  assert.match(card, /\{brands\.length > 0 && \(\s*\/\/ A brand the filter did not stop/);
});

test("a section: its own id or the seller's No section, never a lost value saved as no section", () => {
  const sections = [{ id: 41000001 }, { id: 41000003 }];
  assert.equal(pickOf("41000003", sections), 41000003);
  assert.equal(pickOf("none", sections), "none");
  assert.equal(pickOf("Western Collection", sections), undefined); // not a value of the list: nothing is saved
  assert.equal(pickOf("", sections), undefined);
  assert.equal(sectionLabel({ source: "seller", reason: "", selected_title: "Western Collection" }), "your choice · Western Collection");
  assert.equal(sectionLabel({ source: "seller", reason: "no section, as you chose", selected_title: null }), "your choice · no section, as you chose");
  assert.equal(sectionLabel({ source: "none", reason: "no section fits clearly", selected_title: null }), "no section fits clearly");
  assert.equal(sectionLabel({ source: "suggested", reason: "matches: nurse", selected_title: "Nurse Collection" }), "suggested · matches: nurse");
  assert.equal(SECTION_NOT_SAVED, "Couldn't save the section. Try again.");
  const options = read("../components/ItemOptions.tsx");
  assert.ok(!options.includes("Number(e.target.value)"), "a value that is not a number never becomes null");
  assert.ok(options.includes("began === mark.current && saving.current === 0"), "an older read never overwrites a save");
  assert.ok(read("./api.ts").includes('sections?: Record<string, number | "none" | "default">'), "null is never sent for a section");
});
