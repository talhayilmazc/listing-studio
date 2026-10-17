// Collapsed template cards, the photo viewer, subgroups and the upload's slots (templates page,
// components/PhotoViewer.tsx, components/MockupUpload.tsx; backend pipeline/subgroups.py).
//
// Cards start collapsed and remember which are open in this browser, surviving missing
// storage; the viewer's arrows, keys and swipes move through a template's photos and stop at
// the ends; an area's default subgroup is the server's rule; the upload shows a slot per
// subgroup of a split template and keeps a design's files together.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import {
  OPEN_TEMPLATES_KEY,
  STRIP_PHOTOS,
  areaSubgroup,
  filesBySlot,
  needCheckBadge,
  readOpenTemplates,
  stripPhotos,
  swipeStep,
  uploadSlots,
  viewerCounter,
  viewerStep,
  writeOpenTemplates,
} from "./mockups.ts";
import type { MockupDesign, MockupPhoto, MockupSubgroup } from "./types.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");

function photo(id: string, needs: boolean, status = "ready"): MockupPhoto {
  return {
    id, template_id: "t", name: id, position: 0, status, width: 700, height: 800, mask_source: null, size: 1, fold_strength: 0.5,
    version: 1, confirmed: !needs, needs_check: needs, areas: [],
  };
}

test("which cards are open is remembered, and missing or broken storage leaves them collapsed", () => {
  const box: Record<string, string> = {};
  const storage = { getItem: (k: string) => box[k] ?? null, setItem: (k: string, v: string) => { box[k] = v; } };
  assert.deepEqual(readOpenTemplates(storage), []); // collapsed at first
  writeOpenTemplates(storage, ["a", "b"]);
  assert.deepEqual(readOpenTemplates(storage), ["a", "b"]);
  box[OPEN_TEMPLATES_KEY] = "{not json";
  assert.deepEqual(readOpenTemplates(storage), []);
  const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.deepEqual(readOpenTemplates(throwing), []);
  assert.doesNotThrow(() => writeOpenTemplates(throwing, ["a"]));
  assert.deepEqual(readOpenTemplates(null), []);
});

test("a collapsed card shows up to six ready photos, those needing a check first", () => {
  const photos = [photo("1", false), photo("2", true), photo("3", false, "preparing"), ...Array.from({ length: 8 }, (_, i) => photo(`x${i}`, false))];
  const strip = stripPhotos(photos);
  assert.equal(strip.length, STRIP_PHOTOS);
  assert.equal(strip[0].id, "2");
  assert.ok(!strip.some((p) => p.id === "3"));
  assert.equal(needCheckBadge(1), "1 needs check");
  assert.equal(needCheckBadge(3), "3 need check");
});

test("the viewer's arrows and swipes move one photo and stop at the ends", () => {
  assert.equal(viewerStep(0, 5, 1), 1);
  assert.equal(viewerStep(4, 5, 1), 4);
  assert.equal(viewerStep(0, 5, -1), 0);
  assert.equal(viewerStep(3, 5, -1), 2);
  assert.equal(viewerCounter(2, 5), "3 of 5");
  assert.equal(swipeStep(-80, 10), 1); // swipe left: the next photo
  assert.equal(swipeStep(90, -5), -1);
  assert.equal(swipeStep(20, 0), 0); // too short
  assert.equal(swipeStep(-60, 70), 0); // mostly a scroll
});

test("the viewer: arrow buttons, the keyboard, swipes, Escape and Check from inside", () => {
  const viewer = read("components/PhotoViewer.tsx");
  for (const id of ["viewer-prev", "viewer-next", "viewer-check", "viewer-close"]) assert.ok(viewer.includes(`data-testid="${id}"`), id);
  assert.match(viewer, /ArrowLeft[\s\S]*ArrowRight[\s\S]*Escape/);
  assert.match(viewer, /onTouchStart[\s\S]*onTouchEnd[\s\S]*swipeStep/);
  assert.match(viewer, /min-h-11 min-w-11[^"]*"\s*\n?\s*onClick=\{\(\) => go\(-1\)\}/);
  // Only a width the server serves (pipeline/images.py PREVIEW_WIDTHS); another one is refused with 400.
  const widths = [...viewer.matchAll(/mockupPhotoImageUrl\(photo\.id, (\d+)\)/g)].map((m) => Number(m[1]));
  assert.ok(widths.length > 0 && widths.every((w) => [112, 224, 448, 896].includes(w)), String(widths));
  const page = read("app/(app)/templates/page.tsx");
  // A photo opens the viewer; Check there opens the editor on it.
  assert.match(page, /data-testid="template-photo-open"/);
  assert.match(page, /onClick=\{onView\}/);
  assert.match(page, /onCheck=\{\(p\) => \{ setViewer\(null\); setEditor\(\{ queue: \[p\.id\], index: 0 \}\); \}\}/);
});

test("cards start collapsed, with Expand all and Collapse all, and the open state is kept per browser", () => {
  const page = read("app/(app)/templates/page.tsx");
  assert.match(page, /useState<string\[\]>\(\[\]\)/); // nothing open before storage is read
  assert.match(page, /readOpenTemplates\(stored\)/);
  assert.match(page, /try \{\s*stored = window\.localStorage;/); // storage access guarded
  for (const id of ["templates-expand-all", "templates-collapse-all", "template-toggle", "template-strip", "template-badge"]) {
    assert.ok(page.includes(`data-testid="${id}"`), id);
  }
  assert.match(page, /aria-expanded=\{open\}/);
  assert.match(page, /loading="lazy"/);
});

const groups: MockupSubgroup[] = [
  { id: "w", name: "White", use_on: "light", suffix: "-white", position: 0 },
  { id: "b", name: "Black", use_on: "dark", suffix: "-black", position: 1 },
];

test("an area's default subgroup follows the server's rule: the seller's link, the garment's tone, then the photo's", () => {
  assert.equal(areaSubgroup({ tone: "light" }, "b", groups)?.name, "White");
  assert.equal(areaSubgroup({ tone: "dark" }, "w", groups)?.name, "Black");
  assert.equal(areaSubgroup({ tone: "light", subgroup_id: "b" }, "w", groups)?.name, "Black");
  assert.equal(areaSubgroup({ tone: null }, "b", groups)?.name, "Black");
  assert.equal(areaSubgroup({ tone: "dark" }, "w", [groups[0]])?.name, "White"); // one subgroup: the photo's
  const editor = read("components/PrintAreaEditor.tsx");
  assert.match(editor, /data-testid="editor-area-subgroup"/);
  assert.match(editor, /subgroup_id: a\.subgroup \?\? null/);
});

test("the upload has a slot per subgroup of a split template, and a design's files stay one design", () => {
  assert.deepEqual(uploadSlots([{ subgroups: [groups[0]] }]), []); // not split: no slots
  const slots = uploadSlots([{ subgroups: groups }, { subgroups: [{ ...groups[0], id: "w2", name: "white" }, { ...groups[1], id: "h", name: "Heather" }] }]);
  assert.deepEqual(slots.map((s) => s.name), ["White", "Black", "Heather"]); // one per name
  const design = (id: string, subgroup: string | null, versions: { id: string; subgroup: string; original_filename: string }[] = []) =>
    ({ id, original_filename: `${id}.png`, group_key: id, parsed_sku: id, width: 900, height: 900, has_alpha: true, subgroup, versions }) as MockupDesign;
  const by = filesBySlot([
    design("BR100", "White", [{ id: "v", subgroup: "Black", original_filename: "BR100-black.png" }]),
    design("CR200", null),
  ], slots);
  assert.deepEqual(by.White.map((f) => f.filename), ["BR100.png"]);
  assert.deepEqual(by.Black.map((f) => [f.design.id, f.versionId]), [["BR100", "v"]]);
  assert.deepEqual(by[""].map((f) => f.filename), ["CR200.png"]);
  const upload = read("components/MockupUpload.tsx");
  assert.match(upload, /data-testid="mockup-slot"/);
  assert.match(upload, /draggable=\{!disabled\}/);
  assert.match(upload, /data-testid="mockup-slot-move"/); // phones: Move to, no dragging needed
  assert.match(upload, /upsert\(\[design\]\)/);
});
