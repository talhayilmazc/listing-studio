// Hand-written listings on the review page: "Generate the empty ones" takes only
// listings nothing was typed into; "Approve all" says one summary line and marks the
// cards in place; a hand-written card is stopped only by Etsy's own rules.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { emptyHandWritten, needsById } from "./review.ts";
import type { Content } from "./types.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const card = (over: Partial<Content>): Content =>
  ({ id: "x", title: "", tags: [], description: "Body", approved: false, publications: [], source: "manual", group_key: "g", ...over }) as Content;

test("Generate the empty ones: hand-written listings with no title and no tags, each group once", () => {
  const items = [
    card({ id: "1", group_key: "A" }),
    card({ id: "2", group_key: "B", title: "Typed title" }),
    card({ id: "3", group_key: "C", tags: ["typed tag"] }),
    card({ id: "4", group_key: "D", source: "ai" }),
    card({ id: "5", group_key: "E", publications: [{ connection_id: "s" } as Content["publications"][number]] }),
    card({ id: "6", group_key: "A" }),
  ];
  assert.deepEqual(emptyHandWritten(items), ["A"]);
});

test("each skipped listing is marked with its own short reasons", () => {
  assert.deepEqual(needsById(null), {});
  assert.deepEqual(
    needsById({ skipped: [{ content_id: "1", reason: "title is empty", reasons: ["needs a title"] }, { content_id: "2", reason: "old" }] }),
    { "1": ["needs a title"], "2": ["old"] },
  );
});

test("the page says one summary line and a line per reason, not every listing's list", () => {
  const page = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(page.includes('data-testid="approve-summary"') && page.includes("approveResult.problems"));
  assert.ok(page.includes("!approveResult.summary && approveResult.skipped.length > 0"), "the per-listing list is only an older server's fallback");
  assert.ok(page.includes("Generate the empty ones") && page.includes("onlyEmpty: true"));
  const reviewCard = read("components/ReviewCard.tsx");
  assert.ok(reviewCard.includes('data-testid="card-needs"') && reviewCard.includes("draftErrors(title, tags, description)"));
  assert.ok(reviewCard.includes('data-testid="card-notes"'), "the app's rules are notes on a hand-written card");
});
