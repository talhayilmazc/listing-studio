// Mockup results update live while the design editor stays open (seller feedback, Part A): the
// tile states (lib/mockupTiles.ts), the one way every change reaches the mockups, versioned image
// URLs, the editor's strip and a failed re-render with "Try again". The server side is
// backend/tests/test_mockup_live.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { NEUTRAL, fileFor, lookPixels, printPixels, recolourPixels, scaleTrim, trimOn, type Region } from "./designEdits.ts";
import { editorTiles, keepPolling, pairKey, stampsOf, tileState, updatingLine } from "./mockupTiles.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (f: string) => fs.readFileSync(path.join(FRONTEND, f), "utf8");

const out = (id: string, photo: string, stamp: string) => ({ id, photo_id: photo, template_name: "Ten colours", filename: `${id}.jpg`, available: true, stamp });
const run = (over: Record<string, unknown> = {}) => ({
  status: "succeeded",
  designs: [
    { design_id: "d1", original_filename: "BR1.png", sku: "BR1", group_key: "BR1", done: 2, total: 2, in_listings: false,
      outputs: [out("o1", "p1", "aaa"), out("o2", "p2", "bbb")] },
    { design_id: "d2", original_filename: "BR2.png", sku: "BR2", group_key: "BR2", done: 2, total: 2, in_listings: false,
      outputs: [out("o3", "p1", "ccc"), out("o4", "p2", "ddd")] },
  ],
  refreshing: [] as { design_id: string; photo_id: string; from_stamp?: string }[],
  refresh_failed: [] as { design_id: string; photo_id: string; reason: string }[],
  ...over,
});

test("a tile is updating from the moment it is asked for until its own new file is in", () => {
  // Just asked for (the server has not answered): updating while the stamp is the one it had.
  const asked = stampsOf(run(), [["d1", "p1"], ["d1", "p2"]]);
  assert.deepEqual(asked, { [pairKey("d1", "p1")]: "aaa", [pairKey("d1", "p2")]: "bbb" });
  assert.equal(tileState(run(), "d1", out("o1", "p1", "aaa"), asked).kind, "updating");
  // In the server's queue: updating until its version changes, then ready, even while the rest of
  // the queue is still being made (each tile swaps in on its own).
  const queued = run({ status: "running", refreshing: [{ design_id: "d1", photo_id: "p1", from_stamp: "aaa" }, { design_id: "d1", photo_id: "p2", from_stamp: "bbb" }] });
  assert.equal(tileState(queued, "d1", out("o1", "p1", "aaa")).kind, "updating");
  assert.equal(tileState(queued, "d1", out("o1", "p1", "new")).kind, "ready");
  assert.equal(tileState(queued, "d2", out("o3", "p1", "ccc")).kind, "ready"); // not asked for
  assert.equal(keepPolling(queued), true);
  assert.equal(keepPolling(run({ refreshing: [{ design_id: "d1", photo_id: "p2", from_stamp: "bbb" }] })), true); // between turns
  assert.equal(keepPolling(run()), false);
});

test("a failed re-render says so (with the reason), and asking again makes it updating", () => {
  const failed = run({ refresh_failed: [{ design_id: "d1", photo_id: "p2", reason: "the photo's file could not be read" }] });
  assert.deepEqual(tileState(failed, "d1", out("o2", "p2", "bbb")), { kind: "failed", reason: "the photo's file could not be read" });
  const retried = { ...failed, refreshing: [{ design_id: "d1", photo_id: "p2", from_stamp: "bbb" }] };
  assert.equal(tileState(retried, "d1", out("o2", "p2", "bbb")).kind, "updating");
  assert.equal(updatingLine([{ kind: "failed", reason: "x" }, { kind: "ready" }]), "1 mockup could not be updated.");
  assert.equal(updatingLine([{ kind: "updating" }, { kind: "updating" }, { kind: "ready" }]), "Updating 2 mockups…");
  assert.equal(updatingLine([{ kind: "ready" }]), "Every mockup is up to date.");
});

test("the editor's strip: this design's mockups, then every other one changed while it is open", () => {
  assert.deepEqual(editorTiles(run(), "d1", []).map((t) => t.output.id), ["o1", "o2"]);
  // "All designs on this photo" touched d2 on p1: it stays in the strip, updated live.
  assert.deepEqual(editorTiles(run(), "d1", [pairKey("d2", "p1")]).map((t) => t.output.id), ["o1", "o2", "o3"]);
});

test("the tile preview draws the editor's pipeline: recolour on the whole file, trim, then the look", () => {
  const w = 6;
  const h = 4;
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) data.set([200, 40, 30, i % 3 === 0 ? 0 : 255], i * 4);
  const regions: Region[] = [{ kind: "rect", points: [[0, 0], [0.5, 1]], colour: "#ffffff" }];
  const adjust = { ...NEUTRAL, opacity: 0.6, brightness: 0.1 };
  const trim = [1, 1, 5, 3];
  const printed = printPixels({ data, width: w, height: h }, trim, regions, adjust);
  // The same, by hand.
  const px = new Uint8ClampedArray(data);
  recolourPixels(px, w, h, regions);
  const crop = new Uint8ClampedArray(4 * 2 * 4);
  for (let y = 0; y < 2; y++) crop.set(px.subarray(((1 + y) * w + 1) * 4, ((1 + y) * w + 5) * 4), y * 16);
  lookPixels(crop, adjust);
  assert.deepEqual([printed.width, printed.height], [4, 2]);
  assert.deepEqual(Array.from(printed.data), Array.from(crop));
  assert.deepEqual(Array.from(data.slice(0, 4)), [200, 40, 30, 0]); // the source is never changed
  // A file chosen by subgroup, else the design's own; a trim scaled to the pixels loaded.
  const files = [{ version_id: null, subgroup: "White" }, { version_id: "v2", subgroup: "Black" }];
  assert.equal(fileFor(files, "black").version_id, "v2");
  assert.equal(fileFor(files, "Navy").version_id, null);
  assert.deepEqual(scaleTrim([10, 20, 90, 180], [100, 200], 50, 100), [5, 10, 45, 90]);
  assert.deepEqual(trimOn({ trim: null, trim_size: null }, 30, 40), [0, 0, 30, 40]);
});

test("every change reaches the mockups the same way, the results and the editor sharing one state", () => {
  const editor = read("components/DesignEditor.tsx");
  const upload = read("components/MockupUpload.tsx");
  const tile = read("components/MockupTile.tsx");
  const api = read("lib/api.ts");
  // Save (also Reset and the look), apply and undo all go through remake: preview first, then the run.
  assert.equal((editor.match(/await remake\(/g) ?? []).length, 3);
  assert.match(editor, /await onEdited\(pairs\);\s*try \{\s*onRun\(await api\.rerenderMockups\(runId, pairs\)\);\s*\} catch \(e\) \{\s*onRun\(null\);/);
  assert.doesNotMatch(editor, /onSaved/);
  // The results reload the edits and mark the tiles at once; the server's answer is the state from then on.
  assert.match(upload, /setOptimistic\(\(now\) => \(\{ \.\.\.now, \.\.\.stampsOf\(run, pairs\) \}\)\);/);
  assert.match(upload, /setEdits\(await api\.mockupEdits\(run\.id\)\);/);
  assert.match(upload, /onEdited=\{edited\} onRun=\{ran\}/);
  assert.match(upload, /if \(!run \|\| !keepPolling\(run\)\) return;/); // polls until every mockup is made again
  assert.doesNotMatch(upload, /status: "queued"/); // no made-up local status
  // The editor stays open and shows the strip under the photo; the same tiles as the results.
  assert.match(upload, /strip=\{<EditorStrip /);
  assert.match(editor, /\{strip && <div key="strip"/);
  assert.match(upload, /data-testid="design-editor-tiles"/);
  // Tiles: a preview with "Updating…", a versioned image, a failure with "Try again".
  for (const id of ["mockup-tile", "mockup-tile-updating", "mockup-tile-preview", "mockup-tile-failed", "mockup-tile-retry", "mockup-tile-image"]) {
    assert.ok(tile.includes(`data-testid="${id}"`), id);
  }
  assert.match(tile, /printPixels\(src, trimOn\(file, src\.width, src\.height\), regions, adjust\)/);
  assert.match(tile, /photoPoints\(quad, size\[0\], size\[1\], photo\.size, adjust\)/);
  assert.match(api, /mockupOutputImageUrl: [^]*?query\(\{ size, v: stamp \}\)/); // query() encodes it
  assert.match(api, /req<MockupRun>\(`\/mockup-runs\/\$\{runId\}\/rerender`/);
});
