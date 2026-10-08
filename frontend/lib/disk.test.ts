// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { DISK_COLORS, FREE_COLOR, percent, segments, size } from "./disk.ts";
import type { AdminDisk } from "./types.ts";

const GB = 1024 ** 3;

function disk(bytes: Record<string, number | null>, over: Partial<AdminDisk> = {}): AdminDisk {
  const label: Record<string, string> = { uploads: "Uploads", derivatives: "Derivatives", database: "Database", backups: "Backups", docker: "Docker", other: "System and other" };
  return {
    as_of: "2026-10-04T12:00:00Z", total_bytes: 30 * GB, free_bytes: 9 * GB,
    categories: Object.keys(label).map((key) => ({ key, label: label[key], note: "", bytes: bytes[key] ?? null, files: null })),
    host_reported_at: null, host_fresh: false, retention: { drafted_days: 3, unpublished_days: 30 },
    retention_defaults: { drafted_days: 3, unpublished_days: 30 }, retention_applies: true, last_run: null, accounts: [], growth_per_day: null,
    storage_growth_per_day: null, days_until_full: null, growth_basis_days: 0, ...over,
  };
}

test("sizes read as a person says them; unknown is a dash, never zero", () => {
  assert.equal(size(6.14 * GB), "6.1 GB");
  assert.equal(size(616 * 1024 ** 2), "616 MB");
  assert.equal(size(2048), "2 KB");
  assert.equal(size(0), "0 KB");
  assert.equal(size(null), "—");
  assert.equal(percent(0.304), "30%");
  assert.equal(percent(0.004), "<1%");
  assert.equal(percent(0), "0%");
});

test("the bar is the whole disk: each category, what is left over, then free space", () => {
  const all = segments(disk({ uploads: 6 * GB, derivatives: 2 * GB, database: 0.5 * GB, backups: 3 * GB, docker: 8 * GB, other: 1.5 * GB }));
  assert.deepEqual(all.map((s) => s.key), ["uploads", "derivatives", "database", "backups", "docker", "other", "free"]);
  assert.equal(Math.round(all.reduce((sum, s) => sum + s.share, 0) * 1000), 1000);
  assert.equal(all[0].color, DISK_COLORS.uploads);
  assert.equal(all[6].color, FREE_COLOR);
  assert.equal(all[6].bytes, 9 * GB);
  assert.equal(new Set(["uploads", "derivatives", "database", "backups", "docker"].map((k) => DISK_COLORS[k])).size, 5);
});

test("until the host reports, what cannot be attributed is one segment that says so", () => {
  const partial = segments(disk({ uploads: 6 * GB, derivatives: 2 * GB, database: 0.5 * GB }));
  assert.deepEqual(partial.map((s) => s.key), ["uploads", "derivatives", "database", "unmeasured", "free"]);
  const rest = partial[3];
  assert.equal(rest.bytes, 12.5 * GB);
  assert.equal(rest.label, "Not measured separately yet (backups, docker, system)");
  // A category measured as empty is left out of the bar; the table still lists it.
  assert.deepEqual(segments(disk({ uploads: 0, derivatives: 2 * GB, database: 0, backups: 0, docker: 8 * GB, other: 11 * GB })).map((s) => s.key), ["derivatives", "docker", "other", "free"]);
  assert.deepEqual(segments(disk({}, { total_bytes: null })), []);
});
