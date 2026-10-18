// Mockup templates, the print-area editor and the mockup upload (backend api/mockups.py,
// pipeline/mockups.py).
//
// The words and limits the pages show are the backend's own; the blank is accepted
// only with the seller's confirmation; a template holds many photos; a photo is used
// only once its print areas are confirmed; no upload waits silently (progress, the
// server's error, a timeout); every control is 44 px and works without dragging; and
// nothing on the pages says a model draws or changes the design.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import {
  FOLD_RANGE,
  LOOKS_RIGHT,
  MAX_NAME,
  MAX_PHOTOS_PER_RUN,
  MAX_PHOTOS_PER_TEMPLATE,
  MAX_UPLOAD_MB,
  NEEDS_CHECK,
  RIGHTS_STATEMENT,
  SIZE_RANGE,
  checkLine,
  nameFromFile,
  percent,
  printable,
  runLine,
  sizeProblem,
  skippedLine,
  templateNameFrom,
  waitingForCheck,
} from "./mockups.ts";
import type { MockupPhoto, MockupTemplate } from "./types.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const BACKEND = path.join(FRONTEND, "..", "backend", "app");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const backend = (relative: string) => fs.readFileSync(path.join(BACKEND, relative), "utf8");
const PAGE = "app/(app)/templates/page.tsx";
const UPLOAD = "components/MockupUpload.tsx";
const EDITOR = "components/PrintAreaEditor.tsx";

const photo = (id: string, extra: Partial<MockupPhoto> = {}): MockupPhoto => ({
  id, template_id: "t", name: id, position: 0, status: "ready", width: 100, height: 100, mask_source: "background",
  size: 0.9, fold_strength: 0.5, version: 1, confirmed: true, needs_check: false, view: "front",
  areas: [{ print_area: [[0.3, 0.2], [0.7, 0.2], [0.7, 0.6], [0.3, 0.6]], detected_area: [[0.3, 0.2], [0.7, 0.2], [0.7, 0.6], [0.3, 0.6]], source: "default" }],
  ...extra,
});
const template = (id: string, photos: MockupPhoto[]): MockupTemplate => ({
  id, name: id, position: 0, photos, needs_check: photos.filter((p) => p.needs_check).length,
  confirmed: photos.filter((p) => p.confirmed).length,
});

test("the rights sentence and every limit are the backend's", () => {
  assert.equal(RIGHTS_STATEMENT, "This photo is my own, or I have the right to use it to show my products.");
  const api = backend("api/mockups.py");
  assert.ok(api.includes(`RIGHTS_STATEMENT = "${RIGHTS_STATEMENT}"`));
  assert.match(api, new RegExp(`^MAX_NAME = ${MAX_NAME}$`, "m"));
  const engine = backend("pipeline/mockups.py");
  assert.match(engine, new RegExp(`^SIZE_RANGE = \\(${SIZE_RANGE[0]}, ${SIZE_RANGE[1].toFixed(1)}\\)$`, "m"));
  assert.match(engine, new RegExp(`^FOLD_RANGE = \\(${FOLD_RANGE[0].toFixed(1)}, ${FOLD_RANGE[1].toFixed(1)}\\)$`, "m"));
  assert.match(backend("workers/mockups.py"), new RegExp(`^MAX_PHOTOS = ${MAX_PHOTOS_PER_RUN}$`, "m"));
  assert.match(backend("db/models.py"), new RegExp(`MAX_PER_TEMPLATE: ClassVar\\[int\\] = ${MAX_PHOTOS_PER_TEMPLATE}`));
  assert.match(backend("core/config.py"), new RegExp(`max_mockup_upload_bytes: int = ${MAX_UPLOAD_MB} \\* 1024 \\* 1024`));
  assert.match(backend("core/security.py"), /_MOCKUP_PATH = re\.compile\(r"\^\/api\/\(mockup-templates\/\[\^\/\]\+\/photos\|batches\/\[\^\/\]\+\/designs\)\$"\)/);
});

test("a file over 30 MB is refused before it is sent, saying so", () => {
  assert.equal(sizeProblem({ name: "tee.jpg", size: 25 * 1024 * 1024 }), null);
  assert.equal(sizeProblem({ name: "tee.jpg", size: 31 * 1024 * 1024 }), "tee.jpg is 31.0 MB: the limit is 30 MB per file.");
  // Both pages pick files through lib/fileDrop.ts, which refuses one over 30 MB on its own row.
  assert.match(read("lib/fileDrop.ts"), /file\.size > MAX_UPLOAD_MB \* 1024 \* 1024/);
  for (const file of [PAGE, UPLOAD]) {
    assert.match(read(file), /pick(Files)?\(files/, file);
    assert.match(read(file), /\{LIMIT_NOTE\}/, file);
  }
});

test("no upload waits silently: progress, the server's own error, and a timeout", () => {
  const api = read("lib/api.ts");
  assert.match(api, /export const UPLOAD_TIMEOUT_MS = 180_000;/);
  assert.match(api, /xhr\.timeout = timeoutMs;/);
  assert.match(api, /xhr\.ontimeout = \(\) =>\n\s*reject\(new ApiError\("The upload took too long and was stopped\./);
  assert.match(api, /return postForm<MockupPhoto>\(`\$\{BASE\}\/mockup-templates\/\$\{templateId\}\/photos`, form, onProgress, UPLOAD_TIMEOUT_MS\);/);
  assert.match(api, /return postForm<MockupDesign>\(`\$\{BASE\}\/batches\/\$\{batchId\}\/designs`, form, onProgress, UPLOAD_TIMEOUT_MS\);/);
  const page = read(PAGE);
  assert.match(page, /api\.addMockupPhoto\(templateId, row\.file, row\.name, confirmed, \(pct\) => update\(row\.key, \{ pct \}\)\)/);
  assert.match(page, /update\(row\.key, \{ state: "error", message: String\(e\.message \?\? e\) \}\)/);
  const upload = read(UPLOAD);
  assert.match(upload, /api\.addMockupDesign\(id, u\.file, \(pct\) => update\(u\.key, \{ pct \}\),/);
  assert.match(upload, /update\(u\.key, \{ state: "error", message: String\(e\.message \?\? e\) \}\)/);
  // A run that stops moving for five minutes says so.
  assert.match(upload, /const STALL_MS = 5 \* 60_000;/);
  // The Next.js proxy waits long enough for a large upload.
  assert.match(read("next.config.mjs"), /proxyTimeout: 300_000/);
});

test("a photo appears at once, preparing, the page looks again until it is ready, then it opens for its check", () => {
  const page = read(PAGE);
  assert.match(page, /if \(added\.length\) onAdded\(added\); \/\/ they appear at once, "preparing"; each then opens for its check/);
  assert.match(page, /data-testid="template-preparing"/);
  assert.match(page, /Preparing… finding the garment&apos;s outline/);
  assert.match(page, /const preparing = photos\.some\(\(p\) => p\.status === "preparing"\);/);
  assert.match(page, /Some photos are taking longer than usual to prepare\./);
  // Each new photo opens in the editor when it is ready, in the order they were added.
  assert.match(page, /const queue = ready\.filter\(\(id\) => photos\.find\(\(p\) => p\.id === id\)\?\.needs_check\);/);
  assert.match(page, /if \(queue\.length\) setEditor\(\{ queue, index: 0 \}\);/);
});

test("names up to 120 characters, shown in full on hover; from the file names by default", () => {
  const page = read(PAGE);
  assert.match(page, /maxLength=\{MAX_NAME\}/);
  assert.match(page, /className="min-w-0 truncate text-xs font-medium text-slate-800" title=\{photo\.name\}/);
  assert.equal(nameFromFile("Comfort-Colors_1717 white.png"), "Comfort Colors 1717 white");
  assert.equal(nameFromFile("folder/Kırmızı tişört.jpg"), "Kırmızı tişört");
  assert.equal(nameFromFile("x".repeat(200) + ".png").length, 120);
  assert.equal(templateNameFrom(["Comfort Colors 1717 White", "Comfort Colors 1717 Black"]), "Comfort Colors 1717");
  assert.equal(templateNameFrom(["Front", "Back"]), "Front");
  assert.equal(templateNameFrom(["Only one"]), "Only one");
});

test("templates hold many photos: added together or later, reordered, moved, removed", () => {
  const page = read(PAGE);
  assert.match(page, /api\.createMockupTemplate\(name\.trim\(\) \|\| "Template", presets > 0 && usePresets\)/);
  assert.match(page, /<option value="new">A new template<\/option>/);
  assert.match(page, /<AddPhotos key="add" templates=\{\[\]\} into=\{template\}/); // more photos into a template, later
  assert.match(page, /api\.orderMockupPhotos\(template\.id, ids\)/);
  assert.match(page, /api\.deleteMockupPhoto\(photo\.id\)/);
  assert.match(page, /api\.renameMockupTemplate\(template\.id, name\)/);
  assert.match(page, /api\.deleteMockupTemplate\(template\.id\)/);
  // Colour sets are gone: they became templates (backend migration 0068).
  assert.doesNotMatch(read("lib/api.ts"), /mockup-sets|MockupSet/);
  assert.doesNotMatch(page + read(UPLOAD), /colour set|mockupSets/i);
});

test("a photo is not used until it is confirmed: marked on the card, left out of runs and said so", () => {
  assert.equal(NEEDS_CHECK, "Needs your check");
  const page = read(PAGE);
  assert.match(page, /\{photo\.needs_check \? NEEDS_CHECK : "Confirmed"\}/);
  assert.match(page, /checkLine\(waiting\.length\)\}: they are not used for mockups until you confirm them\./);
  assert.match(page, /data-testid="template-check-all"/);
  // The upload counts only confirmed photos, as the server does, and says how many are left out.
  const t = template("tee", [photo("white"), photo("black", { confirmed: false, needs_check: true }), photo("back", { view: "back" }),
                             photo("prep", { status: "preparing", confirmed: false, needs_check: false })]);
  const both = printable([t, template("other", [photo("x")])], ["tee"], "both");
  assert.deepEqual(both.photos.map((p) => p.id), ["white", "back"]);
  assert.equal(both.skipped, 2);
  assert.deepEqual(printable([t], ["tee"], "back").photos.map((p) => p.id), ["back"]);
  assert.deepEqual(waitingForCheck(t), ["black"]);
  assert.equal(skippedLine(1), "1 photo needs your check and will not be used until you confirm it.");
  assert.equal(checkLine(3), "3 photos need your check");
  const upload = read(UPLOAD);
  assert.match(upload, /const \{ photos: printed, skipped, skippedNames \} = printable\(templates \?\? \[\], chosen, sides\);/);
  assert.match(upload, /<p>\{skippedLine\(skipped\)\}<\/p>/);
  // The server agrees (api/mockups.py start_run).
  assert.match(backend("api/mockups.py"), /if photo\.status == "ready" and photo\.confirmed_at is not None:/);
});

test("the upload section: templates, editable SKUs, per-design progress, both outcomes", () => {
  const upload = read(UPLOAD);
  assert.match(read("app/(app)/upload/page.tsx"), /<MockupUpload shop=\{uploadShop\} \/>/);
  assert.match(upload, /data-testid="mockup-template"/);
  assert.match(upload, /api\.updateMockupDesign\(design\.id, sku\.trim\(\) \|\| null\)/);
  assert.match(upload, /api\.startMockups\(batchId, chosen, \{ sides, garment_mode: garmentMode, design_ids: /);
  assert.match(upload, /<span>\{d\.done\}<\/span><span> \/ <\/span><span>\{d\.total\}<\/span>/);
  assert.match(upload, /api\.useMockupsForListings\(run\.id, ids, covers\)/);
  assert.match(upload, /value=\{o\.photo_id \?\? ""\}/); // the cover is one of the run's photos
  assert.match(upload, /api\.requestMockupZip\(run\.id, ids, layout\)/);
  assert.match(upload, /Kept 24 hours, then deleted\./);
  assert.match(upload, /<option value="folders">A folder per design<\/option>/);
  assert.equal(runLine({ status: "running", done: 3, total: 100, failed: 0 }), "Making mockups: 3 of 100");
  assert.equal(runLine({ status: "succeeded", done: 99, total: 100, failed: 1 }), "99 mockups made, 1 could not be made");
});

test("the editor: large, a big Looks right, every control 44px, by pointer, keyboard and touch", () => {
  const editor = read(EDITOR);
  assert.equal(LOOKS_RIGHT, "Looks right");
  assert.match(editor, /data-testid="editor-confirm">\n\s*\{saving \? "Saving…" : LOOKS_RIGHT\}/);
  assert.match(editor, /className="btn-primary min-h-12 flex-1 text-base sm:flex-none sm:px-10" onClick=\{confirm\}/);
  // Full screen on a phone; at least 900 px wide on a desktop.
  assert.match(editor, /className="fixed inset-0 z-50 /);
  assert.match(editor, /lg:min-w-\[900px\]/);
  // Saves with confirm = true: "Looks right" is what makes the photo usable.
  assert.match(editor, /onConfirmed\(await api\.saveMockupAreas\(photo\.id, payload\(\), true, size, printAll\)\);/);
  // Every button in the editor is at least 44 px (min-h-11, h-11 or min-h-12).
  for (const [tag] of editor.matchAll(/<button[\s\S]*?>/g)) {
    assert.match(tag, /min-h-11|h-11 w-11|min-h-12/, tag);
  }
  // Pointer (mouse and finger), keyboard (arrows, Shift, undo/redo) and no hover-only control.
  assert.match(editor, /onPointerDown=\{\(e\) => begin\(e, \{ kind: "corner"/);
  assert.match(editor, /onPointerDown=\{\(e\) => begin\(e, \{ kind: "rotate"/);
  assert.match(editor, /onPointerDown=\{\(e\) => !tapping && begin\(e, \{ kind: "move"/);
  assert.match(editor, /const by = nudge\(e\.key, e\.shiftKey, W\);/);
  assert.match(editor, /e\.shiftKey \? redo\(now\) : undo\(now\)/);
  assert.doesNotMatch(editor, /hover:opacity|group-hover/);
  // Photo order works without dragging.
  const page = read(PAGE);
  assert.match(page, /aria-label=\{`Move \$\{photo\.name\} earlier`\}/);
  assert.match(page, /aria-label=\{`Move \$\{photo\.name\} later`\}/);
  assert.equal(percent(0.9), "90%");
});

test("the editor's tools: undo, redo, reset to the suggestion, free corners, snap, preview, add and delete areas, copy", () => {
  const editor = read(EDITOR);
  for (const id of ["editor-undo", "editor-redo", "editor-reset", "editor-free", "editor-snap", "editor-preview", "editor-add",
                    "editor-delete", "editor-copy-open", "editor-copy-go", "editor-guide", "editor-rotate", "editor-corner"]) {
    assert.match(editor, new RegExp(`data-testid="${id}"`), id);
  }
  // Snapping: off with Alt held, or with the switch (phones).
  assert.match(editor, /const reach = \(alt: boolean\) => \(snapOn && !alt \? SNAP_SCREEN_PX \/ scale : 0\);/);
  // "Add area": the tap is sent with the areas already there; the new area goes last (tap order).
  assert.match(editor, /api\.findMockupGarment\(photo\.id, x \/ W, y \/ H, areas\.map\(\(a\) => toShares\(a\.quad, W, H\)\),/);
  assert.match(editor, /const next = \[\.\.\.areas, \{ quad, suggested: quad, source: found\.found \? "tap" : "seller", kind: found\.kind, axis: null, role: "front" as const, occlusion: true \}\];/);
  // Copy: this photo saved, then each copied photo opens for its own check.
  assert.match(editor, /const copied = await api\.copyMockupAreas\(photo\.id, copyIds\);/);
  assert.match(read(PAGE), /setEditor\(copied\.length \? \{ queue: copied\.map\(\(p\) => p\.id\), index: 0 \} : null\);/);
  // The seller's own design for the preview stays in the browser.
  assert.match(editor, /Shown here only; nothing is uploaded\./);
});

test("nothing says a model draws or changes the design, or promises anything of Etsy", () => {
  const forbidden = /\b(AI|generat\w*|enhanc\w*|redraw\w* automatically|realistic guarantee|approved by Etsy|Etsy-approved)\b/;
  for (const file of [PAGE, UPLOAD, EDITOR, "lib/mockups.ts"]) {
    for (const [, quoted, shown] of read(file).matchAll(/"([^"\n]*)"|>([^<{}\n]+)</g)) {
      const text = quoted ?? shown;
      assert.doesNotMatch(text.replace("Listings generated", ""), forbidden, `${file}: ${text}`);
    }
  }
  assert.match(read("lib/mockups.ts"), /never redrawn/);
  assert.match(read("lib/mockups.ts"), /Mockups do not count toward Listings generated/);
});

test("templates are in the menu, the page title, the layout test, robots and the Privacy Policy", () => {
  assert.match(read("components/Rail.tsx"), /\{ href: "\/templates", label: "Mockup templates", icon: IconShirt \}/);
  assert.match(read("components/TopStrip.tsx"), /\[\/\^\\\/templates\$\/, "Mockup templates"\]/);
  assert.match(read("e2e/layout.spec.ts"), /"\/templates"/);
  assert.match(read("e2e/layout.spec.ts"), /editor-confirm/); // the editor is in the layout test too
  assert.match(read("app/robots.ts"), /"\/templates"/);
  const privacy = read("app/(marketing)/privacy/page.tsx");
  assert.match(privacy, /blank garment photos you save as mockup templates/);
  assert.match(privacy, /R\.mockupZipHours/);
});

test("front or back per photo, a run on front, back or both, and several garments per photo", () => {
  const editor = read(EDITOR);
  assert.match(editor, /onChange=\{\(e\) => setting\(\{ view: e\.target\.value as "front" \| "back" \}\)\} data-testid="editor-view"/);
  const upload = read(UPLOAD);
  assert.match(upload, /data-testid="mockup-sides"/);
  assert.match(upload, /\{multiGarment && \(/);
  assert.match(upload, /data-testid="mockup-garment-mode"/);
  const api = backend("api/mockups.py");
  assert.match(api, /sides: Literal\["front", "back", "both"\] = "both"/);
  assert.match(api, /garment_mode: Literal\["same", "order"\] = "same"/);
});
