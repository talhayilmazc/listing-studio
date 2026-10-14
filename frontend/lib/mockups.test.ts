// Mockup templates and the mockup upload (backend api/mockups.py, pipeline/mockups.py).
//
// The words and limits the pages show are the backend's own; the blank is accepted
// only with the seller's confirmation; no upload waits silently (progress, the
// server's error, a timeout); every control works without dragging; and nothing on
// the pages says a model draws or changes the design.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import {
  FOLD_RANGE,
  MAX_NAME,
  MAX_TEMPLATES_PER_RUN,
  MAX_UPLOAD_MB,
  RIGHTS_STATEMENT,
  SIZE_RANGE,
  moveCorner,
  nameFromFile,
  percent,
  runLine,
  sizeProblem,
} from "./mockups.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const BACKEND = path.join(FRONTEND, "..", "backend", "app");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const backend = (relative: string) => fs.readFileSync(path.join(BACKEND, relative), "utf8");
const PAGE = "app/(app)/templates/page.tsx";
const UPLOAD = "components/MockupUpload.tsx";

test("the rights sentence and every limit are the backend's", () => {
  assert.equal(RIGHTS_STATEMENT, "This photo is my own, or I have the right to use it to show my products.");
  const api = backend("api/mockups.py");
  assert.ok(api.includes(`RIGHTS_STATEMENT = "${RIGHTS_STATEMENT}"`));
  assert.match(api, new RegExp(`^MAX_NAME = ${MAX_NAME}$`, "m"));
  const engine = backend("pipeline/mockups.py");
  assert.match(engine, new RegExp(`^SIZE_RANGE = \\(${SIZE_RANGE[0]}, ${SIZE_RANGE[1].toFixed(1)}\\)$`, "m"));
  assert.match(engine, new RegExp(`^FOLD_RANGE = \\(${FOLD_RANGE[0].toFixed(1)}, ${FOLD_RANGE[1].toFixed(1)}\\)$`, "m"));
  assert.match(backend("workers/mockups.py"), new RegExp(`^MAX_TEMPLATES = ${MAX_TEMPLATES_PER_RUN}$`, "m"));
  assert.match(backend("core/config.py"), new RegExp(`max_mockup_upload_bytes: int = ${MAX_UPLOAD_MB} \\* 1024 \\* 1024`));
  assert.match(backend("core/security.py"), /_MOCKUP_PATH = re\.compile\(r"\^\/api\/\(mockup-templates\|batches\/\[\^\/\]\+\/designs\)\$"\)/);
});

test("a file over 30 MB is refused before it is sent, saying so", () => {
  assert.equal(sizeProblem({ name: "tee.jpg", size: 25 * 1024 * 1024 }), null);
  assert.equal(sizeProblem({ name: "tee.jpg", size: 31 * 1024 * 1024 }), "tee.jpg is 31.0 MB: the limit is 30 MB per file.");
  for (const file of [PAGE, UPLOAD]) {
    assert.match(read(file), /const problem = sizeProblem\(file\);/, file);
    assert.match(read(file), /\{LIMIT_NOTE\}/, file);
  }
});

test("no upload waits silently: progress, the server's own error, and a timeout", () => {
  const api = read("lib/api.ts");
  assert.match(api, /export const UPLOAD_TIMEOUT_MS = 180_000;/);
  assert.match(api, /xhr\.timeout = timeoutMs;/);
  assert.match(api, /xhr\.ontimeout = \(\) =>\n\s*reject\(new ApiError\("The upload took too long and was stopped\./);
  assert.match(api, /return postForm<MockupTemplate>\(`\$\{BASE\}\/mockup-templates`, form, onProgress, UPLOAD_TIMEOUT_MS\);/);
  assert.match(api, /return postForm<MockupDesign>\(`\$\{BASE\}\/batches\/\$\{batchId\}\/designs`, form, onProgress, UPLOAD_TIMEOUT_MS\);/);
  const page = read(PAGE);
  assert.match(page, /api\.createMockupTemplate\(row\.file, row\.name, confirmed, \(pct\) => update\(row\.key, \{ pct \}\), setId\)/);
  assert.match(page, /update\(row\.key, \{ state: "error", message: String\(e\.message \?\? e\)\ }\)/);
  const upload = read(UPLOAD);
  assert.match(upload, /api\.addMockupDesign\(id, u\.file, \(pct\) => update\(u\.key, \{ pct \}\)\)/);
  assert.match(upload, /update\(u\.key, \{ state: "error", message: String\(e\.message \?\? e\) \}\)/);
  // A run that stops moving for five minutes says so.
  assert.match(upload, /const STALL_MS = 5 \* 60_000;/);
  // The Next.js proxy waits long enough for a large upload.
  assert.match(read("next.config.mjs"), /proxyTimeout: 300_000/);
});

test("a template appears at once, preparing, and the page looks again until it is ready", () => {
  const page = read(PAGE);
  assert.match(page, /onAdded\(\); \/\/ it appears at once, "preparing"/);
  assert.match(page, /data-testid="template-preparing"/);
  assert.match(page, /Preparing… finding the garment&apos;s outline/);
  assert.match(page, /const preparing = \(templates \?\? \[\]\)\.some\(\(t\) => t\.status === "preparing"\);/);
  assert.match(page, /Some templates are taking longer than usual to prepare\./);
});

test("names up to 120 characters, shown in full on hover; from the file name by default", () => {
  const page = read(PAGE);
  assert.match(page, /maxLength=\{MAX_NAME\}/);
  assert.match(page, /className="truncate text-base font-semibold text-slate-900" title=\{template\.name\}/);
  assert.equal(nameFromFile("Comfort-Colors_1717 white.png"), "Comfort Colors 1717 white");
  assert.equal(nameFromFile("folder/Kırmızı tişört.jpg"), "Kırmızı tişört");
  assert.equal(nameFromFile("x".repeat(200) + ".png").length, 120);
});

test("colour sets: uploaded together, one template's settings applied to the set with a preview of each", () => {
  const page = read(PAGE);
  assert.match(page, /api\.createMockupSet\(setName\.trim\(\) \|\| "Colours"\)/);
  assert.match(page, /api\.applyToMockupSet\(template\.set_id, template\.id\)/);
  assert.match(page, />\s*\{applying \? "Applying…" : "Apply these settings to the whole set"\}/);
  assert.match(page, /The whole set with these settings:/);
  // On a phone the long label wraps instead of running past the card.
  assert.match(page, /className="btn-secondary h-auto max-w-full whitespace-normal text-left" onClick=\{applyToSet\}/);
  assert.match(page, /api\.renameMockupSet\(set\.id, name\)/);
  assert.match(page, /api\.deleteMockupSet\(set\.id\)/);
  assert.match(page, /save\(\{ set_id: e\.target\.value \|\| null \}\)/); // moved or taken out of a set
});

test("the upload section: a whole set or any templates, editable SKUs, per-design progress, both outcomes", () => {
  const upload = read(UPLOAD);
  assert.match(read("app/(app)/upload/page.tsx"), /<MockupUpload shop=\{uploadShop\} \/>/);
  assert.match(upload, /data-testid="mockup-set-pick"/);
  assert.match(upload, /api\.updateMockupDesign\(design\.id, sku\.trim\(\) \|\| null\)/);
  assert.match(upload, /api\.startMockups\(batchId, chosen\)/);
  assert.match(upload, /<span>\{d\.done\}<\/span><span> \/ <\/span><span>\{d\.total\}<\/span>/);
  assert.match(upload, /api\.useMockupsForListings\(run\.id, ids, covers\)/);
  assert.match(upload, /api\.requestMockupZip\(run\.id, ids, layout\)/);
  assert.match(upload, /Kept 24 hours, then deleted\./);
  assert.match(upload, /<option value="folders">A folder per design<\/option>/);
  assert.equal(runLine({ status: "running", done: 3, total: 100, failed: 0 }), "Making mockups: 3 of 100");
  assert.equal(runLine({ status: "succeeded", done: 99, total: 100, failed: 1 }), "99 mockups made, 1 could not be made");
});

test("the print area works by pointer, by keyboard and by touch, with 44px handles", () => {
  const page = read(PAGE);
  assert.match(page, /onPointerDown=/);
  assert.match(page, /onKeyDown=\{\(e\) => key\(i, e\)\}/);
  assert.match(page, /className="absolute flex h-11 w-11 [^"]*touch-none/);
  assert.match(page, />Earlier<\/button>/);
  assert.match(page, />Later<\/button>/);
  assert.deepEqual(moveCorner([[0, 0], [1, 0], [1, 1], [0, 1]], 2, 1.4, -0.2), [[0, 0], [1, 0], [1, 0], [0, 1]]);
  assert.equal(percent(0.9), "90%");
});

test("nothing says a model draws or changes the design, or promises anything of Etsy", () => {
  const forbidden = /\b(AI|generat\w*|enhanc\w*|redraw\w* automatically|realistic guarantee|approved by Etsy|Etsy-approved)\b/;
  for (const file of [PAGE, UPLOAD, "lib/mockups.ts"]) {
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
  assert.match(read("app/robots.ts"), /"\/templates"/);
  const privacy = read("app/(marketing)/privacy/page.tsx");
  assert.match(privacy, /blank garment photos you save as mockup templates/);
  assert.match(privacy, /R\.mockupZipHours/);
});
