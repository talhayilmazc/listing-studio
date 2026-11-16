// app/onboarding-v1: the logic behind the new navigation, checklist, review keys, live checks,
// toasts with Undo, empty states, error notes and the admin funnel.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { conversion, waited } from "./funnel.ts";
import { liveChecks } from "./liveChecks.ts";
import { NEEDS_SHOP, needsShop } from "./needsShop.ts";
import { SHORTCUTS, actionFor, formatPrice, move } from "./reviewKeys.ts";
import { approvedLine, queueOf } from "./reviewQueue.ts";
import { MAX, SECONDS, UNDO_SECONDS, doneNote, drop, makeToast, push } from "./toast.ts";
import type { BatchSummary } from "./types.ts";
import { WORDS, writtenLine } from "./words.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("an action keeps its name: Approve gives Approved, with Undo for longer", () => {
  assert.equal(WORDS.approve, "Approve");
  assert.equal(doneNote(WORDS.approved), "Approved");
  assert.equal(doneNote(WORDS.approved, 3), "Approved 3");
  const plain = makeToast("Batch deleted");
  const undo = makeToast("Approved", { action: { label: "Undo", run: () => undefined } });
  const error = makeToast("Could not save", { tone: "error" });
  assert.equal(plain.seconds, SECONDS);
  assert.equal(undo.seconds, UNDO_SECONDS);
  assert.equal(error.seconds, 0, "an error stays until it is closed");
  let list = [plain, undo, error, makeToast("Fourth")];
  list = list.reduce((l, t) => push(l, t), [] as typeof list);
  assert.equal(list.length, MAX);
  assert.equal(list[0].id, undo.id, "the oldest goes first");
  assert.deepEqual(drop(list, undo.id).map((t) => t.message), ["Could not save", "Fourth"]);
  assert.equal(writtenLine(3, 0), "3 listings written.");
  assert.equal(writtenLine(1, 2, 1), "1 listing written, 2 could not be written, 1 already written.");
});

test("reversible actions give Undo, not a question first", () => {
  const card = read("components/ReviewCard.tsx");
  assert.ok(card.includes('toast(now ? "Approved" : "Approval taken back"') && card.includes('label: "Undo"'));
  assert.ok(!card.includes("window.confirm"));
  const review = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(review.includes("doneNote(WORDS.approved, result.approved)") && review.includes('label: "Undo"'));
  assert.ok(read("components/DeleteBatches.tsx").includes("api.restoreBatch(id)"));
  assert.ok(read("components/AppShell.tsx").includes("<Toaster />"));
});

test("the live checks are the free checkers' rules: counters, 13 tags, 20 characters, repeats, trademarks", () => {
  const ok = liveChecks("Tomato Season Shirt, Backyard Garden Harvest", ["gardener gift", "tomato lover", "garden club tee"]);
  assert.ok(ok.checks.every((c) => c.ok), JSON.stringify(ok.checks));
  assert.equal(ok.checks.find((c) => c.key === "count")?.line, "Tags: 3 of 13");
  const bad = liveChecks("x".repeat(150), [...Array.from({ length: 14 }, (_, i) => `tag ${i}`), "a very long tag over twenty", "tag 1"]);
  const by = Object.fromEntries(bad.checks.map((c) => [c.key, c]));
  assert.equal(by.title.ok, false);
  assert.match(by.title.line, /150 of 140 characters, 10 over/);
  assert.equal(by.count.ok, false);
  assert.match(by.length.line, /a very long tag over twenty/);
  assert.match(by.twice.line, /Same tag twice: tag 1/);
  const similar = liveChecks("Shirt", ["nurse shirt", "nurses shirts"]);
  assert.match(similar.checks.find((c) => c.key === "twice")!.line, /Same words twice: nurse shirt \/ nurses shirts/);
  const brand = liveChecks("Disney Mickey Shirt", ["mickey mouse tee"]);
  assert.ok(brand.brands.length > 0 && brand.brands.every((b) => b.includes("is a trademark")));
  // One source of truth: the checker functions, never a second copy.
  const src = read("lib/liveChecks.ts");
  assert.ok(src.includes('from "./etsyText.ts"') && src.includes('from "./listingRules.ts"') && src.includes('from "./brandSpot.ts"'));
});

test("the review keys: J, K, A, E, T and ?; never while typing or with a modifier", () => {
  assert.equal(actionFor({ key: "j" }), "next");
  assert.equal(actionFor({ key: "K" }), "previous");
  assert.equal(actionFor({ key: "a" }), "approve");
  assert.equal(actionFor({ key: "e" }), "title");
  assert.equal(actionFor({ key: "t" }), "tags");
  assert.equal(actionFor({ key: "?" }), "help");
  assert.equal(actionFor({ key: "Escape", target: { tagName: "TEXTAREA" } }), "close");
  assert.equal(actionFor({ key: "a", target: { tagName: "TEXTAREA" } }), null);
  assert.equal(actionFor({ key: "a", target: { tagName: "INPUT", type: "text" } }), null);
  assert.equal(actionFor({ key: "a", target: { tagName: "INPUT", type: "checkbox" } }), "approve");
  assert.equal(actionFor({ key: "a", target: { tagName: "DIV", isContentEditable: true } }), null);
  assert.equal(actionFor({ key: "a", ctrlKey: true }), null);
  assert.equal(actionFor({ key: "t", metaKey: true }), null);
  assert.equal(actionFor({ key: "x" }), null);
  assert.equal(move(-1, 1, 5), 0);
  assert.equal(move(-1, -1, 5), 4);
  assert.equal(move(4, 1, 5), 4);
  assert.equal(move(0, -1, 5), 0);
  assert.equal(move(2, 1, 0), -1);
  assert.deepEqual(SHORTCUTS.map((s) => s.keys), ["J", "K", "A", "E", "T", "?", "Esc"]);
  assert.equal(formatPrice(25, "USD"), "$25.00");
  assert.equal(formatPrice(19.5, null), "$19.50");
});

const batch = (id: string, statuses: string[], extra: Partial<BatchSummary> = {}): BatchSummary =>
  ({ id, status: "ready", statuses, file_count: 1, created_at: `2026-11-0${id}T10:00:00Z`, asset_count: 1, processed_count: 4,
     approved_count: 1, size_chart_profile_id: null, name: `Batch ${id}`, named: false, groups: 4, ...extra }) as BatchSummary;

test("Review lists what waits for approval, then what is approved and not sent; examples are never 'to send'", () => {
  const q = queueOf([
    batch("1", ["in_review"]), batch("2", ["approved"]), batch("3", ["not_written"]), batch("4", ["published"]),
    batch("5", ["approved"], { is_example: true }), batch("6", ["in_review", "approved"]),
  ]);
  assert.deepEqual(q.toApprove.map((b) => b.id), ["6", "1"], "newest first");
  assert.deepEqual(q.toSend.map((b) => b.id), ["2"]);
  assert.deepEqual(q.toWrite.map((b) => b.id), ["3"]);
  assert.equal(approvedLine(batch("1", [])), "1 of 4 approved");
});

test("every empty screen: one sentence and one button", () => {
  assert.equal(needsShop("/profiles", 0), NEEDS_SHOP["/profiles"]);
  assert.equal(needsShop("/profiles", 2), null);
  assert.equal(needsShop("/profiles", null), null, "unknown is not none");
  assert.equal(needsShop("/batches", 0), null);
  for (const line of Object.values(NEEDS_SHOP)) assert.ok(line.endsWith(".") && !line.includes("!"), line);
  const empty = read("components/EmptyState.tsx");
  assert.equal((empty.match(/className="btn-primary"/g) ?? []).length, 2, "one primary button, a link or a button");
  for (const page of ["app/(app)/batches/page.tsx", "app/(app)/review/page.tsx", "app/(app)/scheduled/page.tsx", "app/(app)/batches/[id]/review/page.tsx"]) {
    assert.ok(read(page).includes("<EmptyState"), page);
  }
});

test("the funnel reads as counts, a share of the step before and a median time", () => {
  assert.equal(conversion(null), "–");
  assert.equal(conversion(0.6449), "64%");
  assert.equal(waited(null), "no approvals yet");
  assert.equal(waited(42), "42 s");
  assert.equal(waited(270), "4 min 30 s");
  assert.equal(waited(7500), "2 h 5 min");
  assert.equal(waited(3 * 86400 + 4 * 3600), "3 days 4 h");
  const page = read("app/(app)/admin/page.tsx");
  assert.ok(page.includes('{ id: "funnel", label: "Funnel" }') && page.includes("<FunnelPanel />"));
  assert.ok(!read("components/admin/Funnel.tsx").includes("email"), "no personal data on the funnel");
});

test("an error says what happened, the one action, and the raw answer under Details", () => {
  const note = read("components/ErrorNote.tsx");
  assert.ok(note.includes("<summary") && note.includes("Details") && note.includes("advice.label"));
  assert.ok(!/sorry|oops|unfortunately/i.test(note));
  assert.ok(read("components/ReviewCard.tsx").includes("<ErrorNote"));
  assert.ok(read("components/UnfinishedDrafts.tsx").includes("<ErrorNote"));
});

test("examples: labelled, offered to a new seller, never given a way to Etsy on the page", () => {
  const card = read("components/ReviewCard.tsx");
  assert.ok(card.includes("initial.is_example") && card.includes('data-testid="example-label"'));
  assert.ok(card.includes("missing.length > 0 && !initial.is_example"), "no Create draft on an example");
  const review = read("app/(app)/batches/[id]/review/page.tsx");
  assert.ok(review.includes("!isExample && (") && review.includes('data-testid="example-banner"'));
  assert.ok(read("app/(app)/upload/page.tsx").includes("api.tryExamples()"));
  assert.ok(read("components/SetupChecklist.tsx").includes("api.tryExamples()"));
});

test("sentence case: no upper-case micro labels in the app", () => {
  const css = read("app/globals.css");
  const label = css.slice(css.indexOf("  .label {"), css.indexOf("}", css.indexOf("  .label {")));
  assert.ok(!label.includes("uppercase"));
  for (const file of ["components/ReviewCard.tsx", "components/Rail.tsx", "app/(app)/dashboard/page.tsx", "components/WhereAndWhen.tsx"]) {
    assert.ok(!/\buppercase\b/.test(read(file)), file);
  }
});

test("one button set, radius and shadow scale, visible focus and reduced motion", () => {
  const css = read("app/globals.css");
  for (const token of ["--radius-sm", "--radius-md", "--radius-lg", "--radius-pill", "--shadow-sm", "--shadow-md", "--shadow-lg"]) {
    assert.ok(css.includes(`${token}:`), token);
  }
  for (const button of [".btn-primary {", ".btn-secondary {", ".btn-quiet {", ".btn-danger {"]) assert.ok(css.includes(button), button);
  assert.ok(!css.includes(".btn-ghost"), "the old ghost button is gone");
  assert.ok(css.includes(":focus-visible {") && css.includes("outline: 2px solid var(--ink)"));
  assert.ok(css.includes("@media (prefers-reduced-motion: reduce)"));
});
