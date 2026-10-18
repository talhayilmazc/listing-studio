// The mockup flow (templates page, editor, "Add designs"): many files at once, one failing
// on its own; set the area once for many photos; a clear next step once photos are
// confirmed; designs in bulk; what is in front of the garment in the live preview, and the
// "Keep in front" brush. Nothing here sends anything anywhere but our own server.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { MAX_UPLOAD_MB as DROP_LIMIT, kindOf, pick } from "./fileDrop.ts";
import { ALL_READY, MAX_UPLOAD_MB, RESET_PLACEMENT, LAYOUT_LABELS, applyToTheseLabel, confirmAllLabel, firstConfirmOffer, stillToCheck, addDesignsHref, appliedLine, canAddDesigns, designNote, ownPlacementLine, printable } from "./mockups.ts";
import type { MockupPhoto, MockupTemplate } from "./types.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const PAGE = "app/(app)/templates/page.tsx";
const EDITOR = "components/PrintAreaEditor.tsx";
const UPLOAD = "components/MockupUpload.tsx";
const DESIGNS = "app/(app)/templates/designs/page.tsx";

const file = (name: string, type: string, size = 1000) => ({ name, type, size }) as File;
const photo = (id: string, extra: Partial<MockupPhoto> = {}): MockupPhoto => ({
  id, template_id: "t", name: id, position: 0, status: "ready", width: 100, height: 100, mask_source: "background",
  size: 0.9, fold_strength: 0.5, version: 1, confirmed: true, needs_check: false, view: "front", areas: [], ...extra,
});
const template = (photos: MockupPhoto[]): MockupTemplate => ({
  id: "t", name: "Tee", position: 0, photos, needs_check: photos.filter((p) => p.needs_check).length,
  confirmed: photos.filter((p) => p.confirmed).length,
});

test("many files at once: each says why it cannot be sent, the others go on", () => {
  const picked = pick([
    file("white.png", "image/png"),
    file("notes.txt", "text/plain"),
    file("huge.jpg", "image/jpeg", 31 * 1024 * 1024),
    file("designs.zip", "application/zip"),
    file(".DS_Store", ""),
    file("black.webp", ""),
  ]);
  assert.deepEqual(picked.map((p) => [p.file.name, p.kind, p.problem === null]), [
    ["white.png", "image", true],
    ["notes.txt", "other", false],
    ["huge.jpg", "image", false],
    ["designs.zip", "zip", false], // a ZIP is not a template photo
    ["black.webp", "image", true],
  ]);
  assert.match(picked[2].problem!, /31\.0 MB: the limit is 30 MB per file/);
  assert.equal(DROP_LIMIT, MAX_UPLOAD_MB);
  assert.equal(pick([file("designs.zip", "")], { zip: true })[0].problem, null); // designs may come as a ZIP
  assert.equal(kindOf({ name: "x.JPEG", type: "" }), "image");
  // Drop of files or a folder (opened in parts), and both pickers, on both pages.
  const drop = read("components/FileDrop.tsx");
  assert.match(drop, /webkitdirectory=""/);
  assert.match(drop, /onDrop=\{async \(e\) =>/);
  assert.match(read("lib/fileDrop.ts"), /if \(!batch\.length\) break;/);
  assert.match(read(PAGE), /<FileDrop onFiles=\{choose\}/);
  assert.match(read(UPLOAD), /<FileDrop onFiles=\{choose\} disabled=\{busy \|\| phase === "done"\} zip/);
});

test("set the area once for many photos: select, apply by each one's own garment, confirm selected", () => {
  const page = read(PAGE);
  assert.match(page, /data-testid="template-select-all"/);
  assert.match(page, /data-testid="template-photo-select"/);
  assert.match(page, /await api\.copyMockupAreas\(source\.id, targets, true\)/); // fit: each by its own garment
  assert.match(page, /onChange\(await api\.confirmMockupPhotos\(template\.id, chosen\)\)/);
  assert.match(page, />\n\s*Apply this area to selected\n/);
  assert.match(page, />\n\s*Confirm selected\n/);
  assert.equal(appliedLine(3), "Placed on 3 photos by each one's own garment. Check them, then press Confirm selected.");
  assert.match(read("lib/api.ts"), /body: JSON\.stringify\(\{ photo_ids: photoIds, fit \}\)/);
});

test("Add designs appears once a photo is confirmed, and right after the last check", () => {
  assert.equal(canAddDesigns(template([photo("a", { confirmed: false, needs_check: true })])), false);
  assert.equal(canAddDesigns(template([photo("a"), photo("b", { confirmed: false, needs_check: true })])), true);
  assert.equal(addDesignsHref(["t1", "t2"]), "/templates/designs?templates=t1,t2");
  const page = read(PAGE);
  assert.match(page, /\{canAddDesigns\(template\) && \(\n\s*<Link key="add-designs" href=\{addDesignsHref\(\[template\.id\]\)\} className="btn-primary/);
  assert.match(page, /data-testid="templates-add-designs"/);
  // After "Looks right" on the last photo that needed it.
  assert.equal(ALL_READY, "All photos are ready. Add designs");
  assert.match(page, /if \(!rest\.length\) \{\s*setReadyTemplate\(saved\.template_id\);/);
  assert.match(page, /data-testid="all-ready-add">Add designs<\/Link>/);
  // The editor's previews are never the only action: "Looks right" is always there.
  assert.match(read(EDITOR), /data-testid="editor-confirm"/);
});

test("bulk designs: the step, ZIPs, per-design notes, skipped photos named", () => {
  const designs = read(DESIGNS);
  assert.match(designs, /<MockupUpload key=\{params\.get\(\"run\"\) \?\? \"new\"\} shop=\{selected\?\.id \?\? null\} initialTemplates=\{ids\}/);
  const upload = read(UPLOAD);
  assert.match(upload, /api\.addMockupDesignArchive\(id, u\.file, \(pct\) => update\(u\.key, \{ pct \}\), slots\)/);
  assert.match(upload, /data-testid="mockup-design-failed"/);
  assert.match(upload, /"3\. Create mockups"/);
  assert.match(upload, /`Not used: \$\{\(run\?\.skipped_photos\?\.length \? run\.skipped_photos : skippedNames\)\.join\(", "\)\}`/);
  assert.equal(designNote({ has_alpha: false }), "No transparent background: it is printed as a rectangle.");
  assert.equal(designNote({ has_alpha: true }), null);
  const t = template([photo("White"), photo("Black", { confirmed: false, needs_check: true })]);
  assert.deepEqual(printable([t], ["t"], "both").skippedNames, ["Black"]);
  assert.match(read("components/TopStrip.tsx"), /\[\/\^\\\/templates\\\/designs\$\/, "Add designs"\]/);
  assert.match(read("e2e/layout.spec.ts"), /"\/templates\/designs"/);
});

test("what is in front: the live preview draws it over the design; the brush paints and erases it", () => {
  const editor = read(EDITOR);
  // The front layer sits after the design images and before the guides.
  const front = editor.indexOf('data-testid="editor-front-layer"');
  assert.ok(front > editor.indexOf('data-testid="editor-design"') && front < editor.indexOf("<svg viewBox"));
  assert.match(editor, /photo\.has_occluder && photo\.occlusion !== false/);
  for (const id of ["editor-brush", "editor-brush-paint", "editor-brush-erase", "editor-brush-save", "editor-brush-canvas"]) {
    assert.match(editor, new RegExp(`data-testid="${id}"`), id);
  }
  assert.match(editor, /off\.fillStyle = brushMode === "front" \? "rgb\(255,255,255\)" : "rgb\(0,0,0\)";/);
  assert.match(editor, /onChanged\(await api\.saveFrontBrush\(photo\.id, blob\)\);/);
  // No model, no outside service: the brush goes to our own API only.
  assert.doesNotMatch(editor, /https?:\/\//);
});

test("the seller's own placement is named on the template and Reset says what it keeps", () => {
  assert.equal(ownPlacementLine(1), "Using your own placement from 1 photo");
  assert.equal(ownPlacementLine(4), "Using your own placement from 4 photos");
  assert.match(RESET_PLACEMENT, /standard placement/);
  assert.match(RESET_PLACEMENT, /stay as they are/);
  const page = fs.readFileSync(path.join(import.meta.dirname, "..", "app", "(app)", "templates", "page.tsx"), "utf8");
  assert.match(page, /ownPlacementLine\(template\.own_placement\)/);
  assert.match(page, /resetMockupPlacement/);
});

test("one-time setup: after the first confirmed photo the others are offered; the editor moves on; Confirm all", () => {
  const waiting = (id: string) => photo(id, { confirmed: false, needs_check: true });
  const fresh = template([waiting("a"), waiting("b"), waiting("c")]);
  assert.deepEqual(firstConfirmOffer(fresh, "a"), ["b", "c"]);
  assert.equal(applyToTheseLabel(2), "Apply to these 2 photos");
  // Not the first confirmed photo: no offer, the editor goes on to the next one waiting.
  const later = template([photo("a"), waiting("b"), waiting("c")]);
  assert.equal(firstConfirmOffer(later, "b"), null);
  assert.deepEqual(stillToCheck(later, "b"), ["c"]);
  assert.equal(firstConfirmOffer(template([waiting("a")]), "a"), null);
  assert.equal(confirmAllLabel(5), "Confirm all 5");
  const page = fs.readFileSync(path.join(import.meta.dirname, "..", "app", "(app)", "templates", "page.tsx"), "utf8");
  assert.match(page, /template-photo-layout/);
  assert.match(page, /LAYOUT_LABELS/);
  assert.match(page, /<ConfirmGrid/);
  assert.match(page, /firstConfirmOffer\(before, saved\.id\)/);
  assert.deepEqual(Object.values(LAYOUT_LABELS), ["Single garment", "Front & back", "Several garments"]);
  const grid = fs.readFileSync(path.join(import.meta.dirname, "..", "components", "ConfirmGrid.tsx"), "utf8");
  assert.match(grid, /designQuad/); // the placeholder sits where a mockup puts the design
  const editor = fs.readFileSync(path.join(import.meta.dirname, "..", "components", "PrintAreaEditor.tsx"), "utf8");
  assert.match(editor, /Match garment angle/);
  assert.match(editor, /angleKey\(e\.code, e\.shiftKey\)/);
  assert.match(editor, /editor-angle/);
});
