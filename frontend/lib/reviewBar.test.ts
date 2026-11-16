// The review page's bar: a plain sentence and one primary action (Part H).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { approveAllLabel, approveAllQuestion, approvedLine, fixFor, onScreen, primaryAction, sendBeside, stoppedByBrand, summaryLine } from "./reviewBar.ts";
import { pickOf, sectionLabel, SECTION_NOT_SAVED } from "./sections.ts";

test("the sentence says what will happen, in plain words", () => {
  assert.equal(summaryLine({ listings: 2, shops: 4, when: "drafts" }, 2, 2),
    "2 approved listings will go to 4 shops as drafts. 2 more are waiting for your approval.");
  assert.equal(summaryLine({ listings: 1, shops: 1, when: "publish" }, 1, 0), "1 approved listing will go to 1 shop and go live now.");
  assert.equal(summaryLine({ listings: 0, shops: 0, when: "drafts" }, 3, 0), "Every approved listing is already in its shops.");
  assert.equal(summaryLine({ listings: 0, shops: 0, when: "drafts" }, 0, 3), "Approve listings to send them. 3 listings are waiting for your approval.");
  assert.equal(summaryLine(null, 0, 1), "Approve listings to send them. 1 listing is waiting for your approval.");
});

test("one primary action, and its label says exactly what it approves", () => {
  const on = { selectedUnapproved: 0, currentUnapproved: false, toSend: 0, unapproved: 0, example: false };
  assert.deepEqual(primaryAction({ ...on, selectedUnapproved: 2, currentUnapproved: true, toSend: 4, unapproved: 3 }),
    { kind: "approve-selected", count: 2, label: "Approve 2 selected" });
  // Nothing selected: the listing on screen, said so.
  assert.deepEqual(primaryAction({ ...on, currentUnapproved: true, toSend: 4, unapproved: 3 }), { kind: "approve-this", label: "Approve this listing" });
  // The one on screen is approved: send what is approved; nothing to send: show the next one that waits.
  assert.deepEqual(primaryAction({ ...on, toSend: 4, unapproved: 3 }), { kind: "send", count: 4, label: "Send (4)" });
  assert.deepEqual(primaryAction({ ...on, unapproved: 3 }), { kind: "next", label: "Show the next one to approve" });
  // The examples never go to Etsy: no Send.
  assert.deepEqual(primaryAction({ ...on, toSend: 2, unapproved: 1, example: true }), { kind: "next", label: "Show the next one to approve" });
  assert.equal(primaryAction(on), null);
  // Approved listings can be sent while others wait: "Send (n)" beside the approve action, never twice.
  assert.equal(sendBeside({ kind: "approve-this", label: "" }, 2, false), "Send (2)");
  assert.equal(sendBeside({ kind: "send", count: 2, label: "" }, 2, false), null);
  assert.equal(sendBeside({ kind: "approve-this", label: "" }, 2, true), null);
  assert.equal(sendBeside({ kind: "approve-this", label: "" }, 0, false), null);
});

test("Approve all states its count and asks first; what an approval did is said at once", () => {
  assert.equal(approveAllLabel(15), "Approve all 15 waiting");
  assert.match(approveAllQuestion(15), /^Approve all 15 listings that are waiting\?/);
  assert.match(approveAllQuestion(1), /^Approve all 1 listing that is waiting\?/);
  assert.equal(approvedLine(1, 0, 14), "Approved. 14 waiting.");
  assert.equal(approvedLine(2, 1, 1), "Approved 2. 1 listing can't be approved yet: its card says why. 1 waiting.");
  assert.equal(approvedLine(0, 1, 15), "1 listing can't be approved yet: its card says why. 15 waiting.");
  assert.equal(approvedLine(3, 0, 0), "Approved 3. None waiting.");
  assert.equal(approvedLine(0, 0, 4), "");
  const page = readFileSync(new URL("../app/(app)/batches/[id]/review/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes('data-testid="approve-all-confirm"') && page.includes("run: () => setAskAll(true)"), "Approve all asks before it approves");
  // The bar approves through the server, never by pressing a button on a card the seller may not see.
  assert.ok(!page.includes('[data-testid="approve-toggle"]`)?.click()'), "no hidden click");
  assert.ok(page.includes("approveAll([here.id])"));
});

test("a listing that cannot be approved says why on its card, with the one action that fixes it", () => {
  assert.deepEqual(fixFor("Remove: Mickey (title)"), { field: "title", label: "Fix the title" });
  assert.deepEqual(fixFor("Remove: mickey mouse tee (tag)"), { field: "tags", label: "Fix the tags" });
  assert.deepEqual(fixFor("title is empty"), { field: "title", label: "Fix the title" });
  assert.equal(fixFor("compliance: a character in the drawing"), null);
  const card = readFileSync(new URL("../components/ReviewCard.tsx", import.meta.url), "utf8");
  assert.ok(card.includes('data-testid="approve-top"'), "every card has its own Approve where it begins");
  assert.ok(card.includes('testId="card-blocked"') && card.includes("setRefused("), "a refusal is said on the card");
  assert.ok(!card.includes("disabled={!valid && !approved}"), "the card's Approve is never a dead button: pressed, it says why");
  // The app's notes stay above the bar at the foot of the screen (a note lay over its buttons).
  const toaster = readFileSync(new URL("../components/Toaster.tsx", import.meta.url), "utf8");
  assert.ok(toaster.includes("var(--bottom-bar, 0px)"));
});

test("Keep it, I know the risk: beside the reason, only when a brand name is what stops the listing", () => {
  assert.ok(stoppedByBrand("Remove: Mickey (title)"));
  assert.ok(stoppedByBrand("title is longer than 140 characters; Remove: Disney, Mickey (title)"));
  assert.ok(!stoppedByBrand("title is empty"));
  assert.ok(!stoppedByBrand("compliance: The artwork shows Mickey Mouse."));
  const card = readFileSync(new URL("../components/ReviewCard.tsx", import.meta.url), "utf8");
  assert.ok(card.includes("{stoppedByBrand(why) ? (") && card.includes("{KEEP_LABEL}"), "the same words as Listing health");
  assert.ok(card.includes("api.keepBrands(initial.id"), "one click: recorded on the server, then approved");
  assert.ok(card.includes('data-testid="card-kept-brand"') && card.includes("Stop keeping it"), "the decision is said, with a way back");
  // The warning box is drawn whatever is kept: keeping never hides it.
  assert.match(card, /\{brands\.length > 0 && \(\s*\/\/ A brand the filter did not stop/);
});

test("this listing is the one that fills the screen", () => {
  assert.equal(onScreen([], 800), -1);
  assert.equal(onScreen([{ top: 0, bottom: 2300 }, { top: 2320, bottom: 4600 }], 800), 0);
  // Scrolled: the first card's tail and most of the second.
  assert.equal(onScreen([{ top: -2000, bottom: 300 }, { top: 320, bottom: 2600 }], 800), 1);
  // A short last card at the foot of the page, wholly on screen beside the tail of the one before.
  assert.equal(onScreen([{ top: -900, bottom: 250 }, { top: 270, bottom: 800 }], 800), 1);
  assert.equal(onScreen([{ top: -900, bottom: 500 }, { top: 520, bottom: 800 }], 800), 0);
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
  const options = readFileSync(new URL("../components/ItemOptions.tsx", import.meta.url), "utf8");
  assert.ok(!options.includes("Number(e.target.value)"), "a value that is not a number never becomes null");
  assert.ok(options.includes("began === mark.current && saving.current === 0"), "an older read never overwrites a save");
  const api = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
  assert.ok(api.includes('sections?: Record<string, number | "none" | "default">'), "null is never sent for a section");
});

test("the page: Approve all and Publish all drafts are in More; no Select listings card; notes in Per shop", () => {
  const page = readFileSync(new URL("../app/(app)/batches/[id]/review/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes('data-testid="review-more"'));
  assert.ok(!page.includes("Select listings to approve several at once."));
  assert.ok(!page.includes("production-notes"));
  const bulk = readFileSync(new URL("../components/BulkItemOptions.tsx", import.meta.url), "utf8");
  assert.ok(!bulk.includes('"Select listings"'));
  const card = readFileSync(new URL("../components/ReviewCard.tsx", import.meta.url), "utf8");
  assert.ok(card.includes('data-testid="card-production-note"'));
});

test("#send opens Where and when on arrival and when the address changes to it on the page", () => {
  const page = readFileSync(new URL("../app/(app)/batches/[id]/review/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes('window.addEventListener("hashchange", open)'), "a link to #send on the open page opens the panel too");
});
