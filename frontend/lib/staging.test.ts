// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
// Staging never blocks the upload (A5): the same path is merged by itself, identical
// content is skipped, a changed file is another photo, different folders never clash.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { groupKeyOf, removeGroup, stage, stageArchives, type FileLike } from "./staging.ts";

type FakeFile = FileLike & { content: string };
const f = (relpath: string, content = relpath.split("/").pop()!, lastModified = 1) => ({
  file: { name: relpath.split("/").pop()!, size: content.length, lastModified, content } as FakeFile,
  relpath,
});
// Content fingerprint for the fakes: their text (the page uses SHA-256 of the bytes).
const hashOf = async (file: FakeFile) => file.content;
const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("files group by folder, top-level files under the empty key", async () => {
  assert.equal(groupKeyOf("Designs/BR1/a.png"), "Designs/BR1");
  assert.equal(groupKeyOf("a.png"), "");
  const { groups } = await stage([], [f("BR1/a.png"), f("BR1/b.png"), f("BR2/a.png", "other"), f("x.png")], hashOf);
  assert.deepEqual(groups.map((x) => [x.key, x.files.length]), [["BR1", 2], ["BR2", 1], ["", 1]]);
});

test("two different folders are never the same folder, whatever their SKU or case", async () => {
  const { groups, notes } = await stage([], [
    f("BR6343-COMFORT/front.png", "c1"), f("br6343/front.png", "c2"), f("BR6523-COMFORT/front.png", "c3"),
  ], hashOf);
  assert.deepEqual(groups.map((g) => g.key), ["BR6343-COMFORT", "br6343", "BR6523-COMFORT"]);
  assert.deepEqual(notes, []);
});

test("the same folder added again is merged by itself: new added, identical skipped, changed kept", async () => {
  let { groups } = await stage([], [f("BR6343-COMFORT/front.png", "aaaa"), f("BR6343-COMFORT/back.png", "bbbb")], hashOf);
  const again = await stage(groups, [
    f("BR6343-COMFORT/front.png", "aaaa", 9), // identical content (another date): skipped
    f("BR6343-COMFORT/back.png", "bbbb"), // identical: skipped
    f("BR6343-COMFORT/back.png", "BBBB"), // changed under the same name: another photo
    f("BR6343-COMFORT/side.png", "cccc"),
    f("BR6343-COMFORT/detail.png", "dddd"),
  ], hashOf);
  groups = again.groups;
  assert.deepEqual(groups[0].files.map((x) => x.relpath), [
    "BR6343-COMFORT/front.png", "BR6343-COMFORT/back.png", "BR6343-COMFORT/back (2).png",
    "BR6343-COMFORT/side.png", "BR6343-COMFORT/detail.png",
  ]);
  assert.deepEqual(again.notes, ["3 photos added to BR6343-COMFORT, 2 identical skipped"]);
});

test("loose files from separate picks never collide; only identical content is skipped", async () => {
  const one = await stage([], [f("front.png", "x1"), f("back.png", "x2")], hashOf);
  const two = await stage(one.groups, [f("front.png", "y1"), f("back.png", "x2")], hashOf);
  assert.deepEqual(two.groups[0].files.map((x) => x.relpath), ["front.png", "back.png", "front (2).png"]);
  assert.deepEqual(two.notes, ["1 photo added to the photos without a folder, 1 identical skipped"]);
});

test("adding again appends; it never replaces what is staged", async () => {
  let { groups } = await stage([], [f("BR1/a.png")], hashOf);
  ({ groups } = await stage(groups, [f("BR2/a.png", "z")], hashOf));
  assert.deepEqual(groups.map((x) => x.key), ["BR1", "BR2"]);
});

test("a folder can be removed before uploading", async () => {
  const { groups } = await stage([], [f("BR1/a.png"), f("BR2/a.png", "z")], hashOf);
  assert.deepEqual(removeGroup(groups, "BR1").map((x) => x.key), ["BR2"]);
});

test("the very same ZIP is staged once", () => {
  const z = { name: "a.zip", size: 5, lastModified: 1 };
  assert.equal(stageArchives([z], [z, { ...z, size: 6 }]).length, 2);
});

test("the upload page has no Merge/Skip prompt and nothing blocks the upload", () => {
  const page = read("app/(app)/upload/page.tsx");
  for (const gone of ["added twice", "Merge or skip", "mergePending", "skipPending", "undecided", "Skip the new copy"]) {
    assert.ok(!page.includes(gone), gone);
  }
  assert.ok(page.includes('data-testid="stage-note"'), "one line says what was merged or skipped");
});
