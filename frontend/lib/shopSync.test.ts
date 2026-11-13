// When a shop's sync ends, every page showing its listings updates by itself (Part I).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { anySyncing, finished } from "./shopSync.ts";

const A = "shop-a";
const B = "shop-b";

test("a sync that was running and stopped is announced, once", () => {
  const running = [{ connection_id: A, syncing: true }, { connection_id: B, syncing: false }];
  const ended = [{ connection_id: A, syncing: false, ended_at: "2026-11-13T10:00:00Z" }, { connection_id: B, syncing: false }];
  assert.deepEqual(finished(running, ended), [A]);
  assert.deepEqual(finished(ended, ended), [], "nothing new: nothing announced again");
});

test("a short sync seen only by its end time is announced too", () => {
  const before = [{ connection_id: A, syncing: false, ended_at: "2026-11-13T09:00:00Z" }];
  const after = [{ connection_id: A, syncing: false, ended_at: "2026-11-13T10:00:00Z" }];
  assert.deepEqual(finished(before, after), [A]);
});

test("still running, or not seen before: nothing yet; following stops when nothing syncs", () => {
  assert.deepEqual(finished([{ connection_id: A, syncing: true }], [{ connection_id: A, syncing: true }]), []);
  assert.deepEqual(finished([], [{ connection_id: A, syncing: false, ended_at: "2026-11-13T10:00:00Z" }]), []);
  assert.equal(anySyncing([{ connection_id: A, syncing: false }]), false);
});

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("every page that shows a shop's listings reads them again when its sync ends", () => {
  for (const path of ["../app/(app)/profiles/page.tsx", "../app/(app)/dashboard/page.tsx", "../components/MetricStrip.tsx", "../components/ShopProvider.tsx"]) {
    assert.ok(read(path).includes("onShopSynced("), `${path} listens for the end of a sync`);
  }
  assert.ok(read("../components/AppShell.tsx").includes("<ShopSyncWatcher />"), "one watcher in the app frame");
});

test("Profiles follows the server, not a guess: no fixed looks after Sync, no 'stale' as 'syncing'", () => {
  const page = read("../app/(app)/profiles/page.tsx");
  assert.ok(!page.includes("setTimeout(loadListings"), "no looks at 5 and 15 seconds");
  assert.ok(!page.includes("setSyncing(res.stale)"), "an empty shop is not 'syncing' for good");
  assert.ok(page.includes("setSyncing(Boolean(res.syncing))"));
});
