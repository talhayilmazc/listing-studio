// Generate, Regenerate and "Generate the empty ones" answer at once and the page follows the run (fix/perf-and-memory).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import type { Writing } from "./types.ts";
import { going, outcomeLine, waitForWriting } from "./writing.ts";
import { writtenLine } from "./words.ts";
import { preparingLine, stillPreparing } from "./preparing.ts";

const run = (over: Partial<Writing>): Writing =>
  ({ state: "queued", total: 2, done: 0, written: 0, failed: 0, skipped: 0, message: null, failures: [], ...over });

test("the page follows a run until it stops, and a failed look is tried again", async () => {
  const seen: string[] = [];
  const answers = [run({ state: "running", done: 1 }), null, run({ state: "done", done: 2, written: 2 })];
  const end = await waitForWriting(async () => {
    const next = answers.shift();
    if (!next) throw new Error("dropped");
    return next;
  }, run({}), (w) => seen.push(`${w.state}:${w.done}`), async () => {});
  assert.equal(end.state, "done");
  assert.deepEqual(seen, ["queued:0", "running:1", "done:2"]);
  assert.ok(going(run({})) && !going(end));
});

test("the outcome says why a regenerate left a group alone", () => {
  assert.equal(outcomeLine(run({ state: "done", written: 2 }), writtenLine), "2 listings written.");
  assert.equal(outcomeLine(run({ state: "done", skipped: 1,
    skipped_groups: [{ group_key: "G1", reason: "its content is approved; confirm to replace it" }] }), writtenLine),
  "its content is approved; confirm to replace it");
  assert.equal(outcomeLine(run({ state: "done", total: 0, message: "Every group is already written." }), writtenLine),
    "Every group is already written.");
  assert.equal(outcomeLine(run({ state: "idle" }), writtenLine), null);
});

test("photos still being prepared are counted and said", () => {
  const assets = [{ status: "processed" }, { status: "uploaded" }, { status: "uploaded" }, { status: "failed" }];
  assert.equal(stillPreparing(assets), 2);
  assert.equal(preparingLine(assets), "Preparing photos: 2 of 4 ready. Writing can start when they are all ready.");
  assert.equal(preparingLine([{ status: "processed" }]), null);
});

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("no page waits on /generate for the listings: each follows the run", () => {
  assert.match(read("./api.ts"), /req<Writing>\(`\/batches\/\$\{id\}\/generate`/);
  const batch = read("../app/(app)/batches/[id]/page.tsx");
  assert.equal((batch.match(/setWriting\(await api\.generate\(/g) ?? []).length, 2);
  assert.ok(batch.includes("preparingLine(batch.assets)"));
  const review = read("../app/(app)/batches/[id]/review/page.tsx");
  assert.ok(review.includes("waitForWriting(() => api.batchWriting(id)"));
  assert.ok(!/for \(const \[n, key\] of keys\.entries\(\)\)/.test(review), "one request, not one per group");
});
