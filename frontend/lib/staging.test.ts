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

// --- B1: arranging photos into listings before the upload ------------------------------------

import { arranged, listingsOf, movePhotos, newListingKey, underListing } from "./staging.ts";
import { place } from "./grouping.ts";

test("the cards are the listings the photos become in the chosen mode", async () => {
  const { groups } = await stage([], [f("BR5229-1.png", "a"), f("BR5229 copy.png", "b"), f("BR7001.png", "c"), f("IMG_4411.jpg", "d")], hashOf);
  const bySku = listingsOf(groups, "sku", place);
  assert.deepEqual(bySku.map((l) => [l.key, l.files.length]), [["BR5229", 2], ["BR7001", 1], ["~unsorted", 1]]);
  assert.deepEqual(listingsOf(groups, "one", place).map((l) => [l.key, l.files.length]), [["", 4]]);
});

test("dragging a photo onto another listing moves it there, exactly", async () => {
  const { groups } = await stage([], [f("BR5229-1.png", "a"), f("BR5229-2.png", "b"), f("BR7001.png", "c")], hashOf);
  const photo = groups[0].files.find((sf) => sf.relpath === "BR5229-2.png")!.file;
  const moved = movePhotos(groups, "sku", place, [photo], "BR7001");
  const after = listingsOf(moved.groups, "folder", place);
  assert.deepEqual(after.map((l) => [l.key, l.files.map((x) => x.relpath)]), [
    ["BR5229", ["BR5229/BR5229-1.png"]], ["BR7001", ["BR7001/BR7001.png", "BR7001/BR5229-2.png"]],
  ]);
});

test("dropping on + New listing (or beside the cards) starts a listing; new files can go onto one", async () => {
  const { groups } = await stage([], [f("BR5229-1.png", "a"), f("BR5229-2.png", "b")], hashOf);
  const photo = groups[0].files[1].file;
  const fresh = movePhotos(groups, "sku", place, [photo], null);
  assert.equal(fresh.key, "New listing");
  assert.deepEqual(listingsOf(fresh.groups, "folder", place).map((l) => l.key), ["BR5229", "New listing"]);
  assert.equal(newListingKey(fresh.groups), "New listing 2");
  // New photos dropped onto the BR5229 card (or its "+") join it.
  const onto = underListing(fresh.groups, "folder", place, [f("extra.png", "e")], "BR5229");
  const merged = await stage(onto.base, onto.incoming, hashOf);
  assert.deepEqual(listingsOf(merged.groups, "folder", place).find((l) => l.key === "BR5229")!.files.map((x) => x.relpath),
    ["BR5229/BR5229-1.png", "BR5229/extra.png"]);
});

test("mixed photos, folders and ZIPs stage together; arranging keeps the folders' listings", async () => {
  const { groups } = await stage([], [f("Comfort/BR1/front.png", "1"), f("Comfort/BR1/back.png", "2"), f("BR7001.png", "3")], hashOf);
  const base = arranged(groups, "folder", place);
  assert.deepEqual(listingsOf(base, "folder", place).map((l) => [l.key, l.files.length]),
    listingsOf(groups, "folder", place).map((l) => [l.key, l.files.length]));
});

test("the upload page: no shop, one Add files button, the listings arranged in place", () => {
  const page = read("app/(app)/upload/page.tsx");
  for (const gone of ["For shop", "ShopPicker", "Add photos", "Add folder", "Add ZIP files", "Choose ZIP files", "uploadShop"]) {
    assert.ok(!page.includes(gone), gone);
  }
  assert.ok(page.includes('data-testid="add-files"') && page.includes("<StagingBoard") && page.includes("api.createBatch(null)"));
  const board = read("components/StagingBoard.tsx");
  for (const piece of ['data-testid="new-listing"', 'data-testid="add-to-listing"', 'data-testid="move-to"', "e.target === e.currentTarget"]) {
    assert.ok(board.includes(piece), piece);
  }
});
