// Batches and flow (seller feedback Part D): the Batches page's status filter (several statuses,
// a count each, kept in the URL; lib/batchStatus.ts) and the mockup notification (lib/mockupRuns.ts,
// components/MockupRunWatcher.tsx).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { BATCH_STATUSES, counts, matches, parseStatuses, statusParam, withStatuses } from "./batchStatus.ts";
import { STORE_KEY, designAnchor, readRuns, readyLine, resultsHref, watch, writeRuns } from "./mockupRuns.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (f: string) => fs.readFileSync(path.join(FRONTEND, f), "utf8");
const BACKEND_STATUSES = fs.readFileSync(path.join(FRONTEND, "..", "backend", "app", "pipeline", "batch_status.py"), "utf8");

test("every status the server knows is offered, in the same order", () => {
  const server = /STATUSES: tuple\[str, \.\.\.\] = \(([^)]*)\)/s.exec(BACKEND_STATUSES)![1].match(/"([a-z_]+)"/g)!.map((s) => s.slice(1, -1));
  assert.deepEqual(BATCH_STATUSES.map((s) => s.key), server);
  for (const k of ["published", "scheduled", "drafted", "deleted", "failed", "half_made"]) assert.ok(server.includes(k), k);
});

test("several statuses at once, kept in the URL, unknown ones ignored", () => {
  assert.deepEqual(parseStatuses("published,drafted,bogus,drafted"), ["drafted", "published"]);
  assert.equal(statusParam(["half_made", "failed"]), "half_made,failed");
  assert.equal(withStatuses("?q=tees", ["published", "failed"]), "?q=tees&status=published%2Cfailed");
  assert.equal(withStatuses("?status=failed", []), "");
  assert.equal(matches(["drafted", "published"], ["published"]), true);
  assert.equal(matches(["drafted"], ["failed", "half_made"]), false);
  assert.equal(matches(["drafted"], []), true); // none chosen: every batch
});

test("a count per status (a batch counts once in each of its statuses)", () => {
  const c = counts([{ statuses: ["drafted", "published"] }, { statuses: ["drafted"] }, { statuses: [] }]);
  assert.equal(c.drafted, 2);
  assert.equal(c.published, 1);
  assert.equal(c.failed, 0);
});

test("the Batches page filters by status and keeps it in the URL", () => {
  const page = read("app/(app)/batches/page.tsx");
  assert.match(page, /parseStatuses\(/);
  assert.match(page, /withStatuses\(/);
  assert.match(page, /data-testid="batch-status-filter"/);
  assert.match(page, /matches\(b\.statuses, chosen\)/);
});

test("finished mockup runs: watched from every page, a notification with Show", () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
  writeRuns(storage, watch([], { id: "r1", batch_id: "b1" }));
  assert.equal(readRuns(storage)[0].id, "r1");
  assert.ok(store.has(STORE_KEY));
  assert.equal(readRuns({ getItem: () => "not json" }).length, 0);
  assert.equal(readyLine({ status: "succeeded", done: 12, failed: 0, designs: [1, 2, 3] }), "Mockups ready: 12 mockups for 3 designs.");
  assert.match(readyLine({ status: "succeeded", done: 5, failed: 1, designs: [1] }), /1 could not be made/);
  assert.equal(resultsHref("r1", "d9"), "/templates/designs?run=r1&design=d9");
  assert.equal(designAnchor("d9"), "design-d9");
  const layout = read("app/(app)/layout.tsx");
  assert.match(layout, /<MockupRunWatcher \/>/);
  const watcher = read("components/MockupRunWatcher.tsx");
  assert.match(watcher, /data-testid="mockup-notification-show"/);
});

test("choosing designs: ticks, Select all and none, a count, and a mockup count that follows subgroups", async () => {
  const { countLine, mockupCount, printsOn, selectionLine } = await import("./mockups.ts");
  assert.equal(selectionLine(3, 8), "3 of 8 designs selected");
  assert.equal(countLine(2, 4, 8), "2 designs × 4 photos = 8 mockups");
  assert.match(countLine(2, 4, 6), /→ 6 mockups/);
  // A design made only for the "White" subgroup prints on the white photo, not the black one.
  const templates = [{ id: "t", subgroups: [{ id: "w", name: "White", use_on: "light", suffix: "-white", position: 0 },
    { id: "b", name: "Black", use_on: "dark", suffix: "-black", position: 1 }] }];
  const photo = (id: string, subgroup: string) => ({ id, template_id: "t", subgroup_id: subgroup, areas: [{ print_area: [], detected_area: [] }] }) as any;
  const photos = [photo("p1", "w"), photo("p2", "b")];
  const onlyWhite = { subgroup: "White", versions: [] };
  const both = { subgroup: "White", versions: [{ id: "v", subgroup: "Black", original_filename: "x-black.png" }] };
  const plain = { subgroup: null, versions: [] };
  assert.equal(printsOn(onlyWhite, ["Black"]), false);
  assert.equal(mockupCount([onlyWhite, both, plain], photos, templates as any), 1 + 2 + 2);
  const upload = read("components/MockupUpload.tsx");
  for (const id of ["design-pick", "mockup-select-all", "mockup-select-none", "mockup-design-count", "mockup-count", "mockup-result"]) {
    assert.ok(upload.includes(`data-testid="${id}"`), id);
  }
  assert.match(upload, /design_ids: ticked\.length === designs\.length \? null : ticked\.map\(\(d\) => d\.id\)/);
  assert.match(upload, /announceRun\(started\)/);
  assert.match(upload, /id=\{designAnchor\(d\.design_id\)\}/); // the notification scrolls here and highlights it
});
