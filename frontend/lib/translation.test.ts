// Run with: npm test. Page text must survive browser translators (scripts/translation-safety.cjs).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

test("dynamic and conditional text is in elements React owns", () => {
  const r = spawnSync(process.execPath, ["scripts/translation-safety.cjs"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stdout + r.stderr);
});
