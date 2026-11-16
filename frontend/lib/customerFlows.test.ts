// Production bug, 2026-10-27: a hand-written batch published in one shop showed "no title yet"
// and "Generate the empty ones" for a second shop. One rule for a listing's text in a shop (the
// shop's own, else the listing's), Generate only for what is really empty there and only after
// saying what it uses, the production partner note quiet, approval per shop.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";
import { emptyHandWritten, textFor } from "./review.ts";
import type { Content } from "./types.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const read = (path: string) => readFileSync(join(root, path), "utf8");

const listing = (over: Partial<Content>): Content =>
  ({ id: "a", group_key: "PCS417", source: "manual", title: "Retro Tee", tags: ["a"], description: "Body", publications: [], ...over }) as Content;

describe("one rule for a listing's text in a shop", () => {
  it("is the shop's own field, else the listing's", () => {
    const c = listing({ shop_texts: { ilc: { title: "ILC title", tags: null } } });
    assert.deepEqual(textFor(c, "ilc"), { title: "ILC title", tags: ["a"], description: "Body" });
    assert.deepEqual(textFor(c, "pcs"), { title: "Retro Tee", tags: ["a"], description: "Body" });
  });
  it("a hand-written listing with its text is never empty for Generate, in any shop", () => {
    assert.deepEqual(emptyHandWritten([listing({})], "ilc"), []);
    assert.deepEqual(emptyHandWritten([listing({})], null), []);
  });
  it("only a listing with nothing typed anywhere is empty; text typed for one shop counts", () => {
    const empty = listing({ title: "", tags: [], description: "" });
    assert.deepEqual(emptyHandWritten([empty], "ilc"), ["PCS417"]);
    assert.deepEqual(emptyHandWritten([{ ...empty, shop_texts: { ilc: { title: "Typed" } } }], "pcs"), []);
    assert.deepEqual(emptyHandWritten([{ ...empty, publications: [{} as never] }], null), []);
    assert.deepEqual(emptyHandWritten([{ ...empty, source: "ai" }], null), []);
  });
});

describe("the pages", () => {
  const review = read("app/(app)/batches/[id]/review/page.tsx");
  const batch = read("app/(app)/batches/[id]/page.tsx");
  it("Generate says how many and what it uses of the allowance before it writes", () => {
    assert.ok(review.includes("emptyHandWritten(items ?? [], inView?.id ?? null)"));
    assert.ok(review.includes("onClick={() => setAskEmpty(true)}"));
    assert.ok(!review.includes("onClick={generateEmpty}"));
    assert.ok(batch.includes("setAskGenerate({ count: todo, run: generateAll })"));
    assert.ok(batch.includes("setAskGenerate({ count: 1, run: () => generate(g.key) })"));
    const confirm = read("components/GenerateConfirm.tsx");
    assert.ok(confirm.includes("Each counts as one listing generated"));
    assert.ok(confirm.includes("Listings you typed into are never written over."));
  });
  it("the production partner note is quiet: a note, linked, never a problem", () => {
    assert.ok(review.includes("Note: the profile"));
    assert.ok(review.includes('className="space-y-1 text-xs text-slate-600" data-testid="production-notes"'));
  });
  it("designs already written in another batch are pointed to, on the batch and review pages", () => {
    assert.ok(review.includes("<WrittenElsewhere"));
    assert.ok(batch.includes("<WrittenElsewhere"));
  });
  it("approval is per shop: the workspace approves for the shop, the card says approve again", () => {
    assert.ok(read("components/ShopWorkspace.tsx").includes("api.workspaceApprove(batchId, shop.id, unapproved)"));
    assert.ok(read("components/ReviewCard.tsx").includes("Edited for ${textShop.name}: approve again."));
  });
});
