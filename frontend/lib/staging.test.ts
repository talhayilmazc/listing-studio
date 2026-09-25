// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { groupKeyOf, mergePending, removeGroup, skipPending, stage, stageArchives } from "./staging.ts";

const f = (relpath: string, size = 10, lastModified = 1) => ({
  file: { name: relpath.split("/").pop()!, size, lastModified },
  relpath,
});

test("files group by folder, top-level files under the empty key", () => {
  assert.equal(groupKeyOf("Designs/BR1/a.png"), "Designs/BR1");
  assert.equal(groupKeyOf("a.png"), "");
  const g = stage([], [f("BR1/a.png"), f("BR1/b.png"), f("BR2/a.png"), f("x.png")]);
  assert.deepEqual(g.map((x) => [x.key, x.files.length]), [["BR1", 2], ["BR2", 1], ["", 1]]);
});

test("adding again appends; it never replaces what is staged", () => {
  let g = stage([], [f("BR1/a.png")]);
  g = stage(g, [f("BR2/a.png")]);
  assert.deepEqual(g.map((x) => x.key), ["BR1", "BR2"]);
});

test("the same folder added again waits for merge or skip", () => {
  let g = stage([], [f("BR1/a.png")]);
  g = stage(g, [f("BR1/b.png"), f("BR1/a.png", 99)]);
  assert.equal(g[0].files.length, 1);
  assert.equal(g[0].pending.length, 2);

  const merged = mergePending(g, "BR1");
  assert.deepEqual(merged[0].files.map((x) => [x.relpath, x.file.size]), [["BR1/a.png", 10], ["BR1/b.png", 10], ["BR1/a.png", 99]]);
  assert.equal(merged[0].pending.length, 0);

  const skipped = skipPending(g, "BR1");
  assert.equal(skipped[0].files.length, 1);
  assert.equal(skipped[0].pending.length, 0);
});

test("exactly the same files again need no decision", () => {
  let g = stage([], [f("BR1/a.png")]);
  g = stage(g, [f("BR1/a.png")]);
  assert.equal(g[0].pending.length, 0);
});

test("a folder can be removed before uploading", () => {
  const g = removeGroup(stage([], [f("BR1/a.png"), f("BR2/a.png")]), "BR1");
  assert.deepEqual(g.map((x) => x.key), ["BR2"]);
});

test("the very same ZIP is staged once", () => {
  const z = { name: "a.zip", size: 5, lastModified: 1 };
  assert.equal(stageArchives([z], [z, { ...z, size: 6 }]).length, 2);
});
