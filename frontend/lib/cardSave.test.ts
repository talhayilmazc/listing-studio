// Saving a review card's text as it is typed (lib/cardSave.ts): only what changed,
// with its base; nothing typed is lost while a save is on its way; every card on the
// page saves before "Approve all".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { afterSave, anyUnsaved, flushAll, patchFor, registerCard } from "./cardSave.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const empty = { title: "", tags: [], description: "Body" };

test("a save sends only the fields that changed, each with what the server had", () => {
  assert.equal(patchFor(empty, empty), null);
  assert.deepEqual(patchFor({ ...empty, title: "Frog Tee" }, empty), { title: "Frog Tee", base: { title: "" } });
  assert.deepEqual(patchFor({ ...empty, tags: ["frog"] }, empty), { tags: ["frog"], base: { tags: [] } });
});

test("what is typed while a save is on its way stays; the server's description follows the title", () => {
  const before = empty;
  const sent = patchFor({ ...empty, title: "Frog Tee" }, before)!;
  const server = { title: "Frog Tee", tags: [], description: "Frog Tee\n\nBody" };
  // Nothing typed meanwhile: the card takes the server's text (the description followed the title).
  assert.deepEqual(afterSave(sent, server, { ...empty, title: "Frog Tee" }, before), server);
  // More typed in the title and tags meanwhile: kept, saved next.
  const onCard = { title: "Frog Tee Shirt", tags: ["frog"], description: "Body" };
  assert.deepEqual(afterSave(sent, server, onCard, before), { title: "Frog Tee Shirt", tags: ["frog"], description: "Frog Tee\n\nBody" });
});

test("every card saves before the page acts, and the page knows when something is unsaved", async () => {
  const saved: string[] = [];
  let pending = true;
  const off = registerCard("a", { flush: async () => { saved.push("a"); pending = false; }, unsaved: () => pending });
  registerCard("b", { flush: async () => { throw new Error("offline"); }, unsaved: () => false });
  assert.equal(anyUnsaved(), true);
  await flushAll(); // one card failing does not stop the others
  assert.deepEqual(saved, ["a"]);
  assert.equal(anyUnsaved(), false);
  off();
});

test("the card saves by itself, on leaving, and the page flushes before acting", () => {
  const card = read("components/ReviewCard.tsx");
  for (const piece of ["SAVE_AFTER_MS", "registerCard(", "keepalive: true", "patchFor(", "afterSave("]) {
    assert.ok(card.includes(piece), piece);
  }
  const page = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(page.includes("await flushAll()") && page.includes("anyUnsaved()") && page.includes("beforeunload"));
  const approve = page.slice(page.indexOf("async function approveAll"));
  assert.ok(approve.indexOf("await flushAll()") < approve.indexOf("api.approveAll("), "Approve all saves every card first");
});
