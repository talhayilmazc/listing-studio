// Mockup templates and the mockup upload (backend api/mockups.py, pipeline/mockups.py).
//
// The words and limits the pages show are the backend's own; the blank is accepted
// only with the seller's confirmation; every control works without dragging; and
// nothing on the pages says a model draws or changes the design.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { FOLD_RANGE, MAX_TEMPLATES_PER_RUN, RIGHTS_STATEMENT, SIZE_RANGE, moveCorner, percent, runLine } from "./mockups.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const BACKEND = path.join(FRONTEND, "..", "backend", "app");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const backend = (relative: string) => fs.readFileSync(path.join(BACKEND, relative), "utf8");
const PAGE = "app/(app)/templates/page.tsx";
const UPLOAD = "components/MockupUpload.tsx";

test("the rights sentence and the limits are the backend's", () => {
  assert.equal(RIGHTS_STATEMENT, "This photo is my own, or I have the right to use it to show my products.");
  assert.ok(backend("api/mockups.py").includes(`RIGHTS_STATEMENT = "${RIGHTS_STATEMENT}"`));
  const engine = backend("pipeline/mockups.py");
  assert.match(engine, new RegExp(`^SIZE_RANGE = \\(${SIZE_RANGE[0]}, ${SIZE_RANGE[1].toFixed(1)}\\)$`, "m"));
  assert.match(engine, new RegExp(`^FOLD_RANGE = \\(${FOLD_RANGE[0].toFixed(1)}, ${FOLD_RANGE[1].toFixed(1)}\\)$`, "m"));
  assert.match(backend("workers/mockups.py"), new RegExp(`^MAX_TEMPLATES = ${MAX_TEMPLATES_PER_RUN}$`, "m"));
});

test("a blank is added only with the seller's confirmation", () => {
  const page = read(PAGE);
  assert.match(page, /<span>\{RIGHTS_STATEMENT\}<\/span>/);
  assert.match(page, /disabled=\{!file \|\| !confirmed \|\| busy\}/);
  assert.match(page, /api\.createMockupTemplate\(file, name, confirmed\)/);
});

test("the print area works by pointer, by keyboard and by touch, with 44px handles", () => {
  const page = read(PAGE);
  assert.match(page, /onPointerDown=/);
  assert.match(page, /onKeyDown=\{\(e\) => key\(i, e\)\}/);
  assert.match(page, /className="absolute flex h-11 w-11 [^"]*touch-none/);
  assert.match(page, /aria-label=\{`\$\{CORNER_NAMES\[i\]\} corner of the print area`\}/);
  // Order by buttons, not dragging.
  assert.match(page, />Earlier<\/button>/);
  assert.match(page, />Later<\/button>/);
  assert.deepEqual(moveCorner([[0, 0], [1, 0], [1, 1], [0, 1]], 2, 1.4, -0.2), [[0, 0], [1, 0], [1, 0], [0, 1]]);
  assert.equal(percent(0.9), "90%");
});

test("the upload section prints on the chosen templates in the seller's order and shows progress", () => {
  const upload = read(UPLOAD);
  assert.match(read("app/(app)/upload/page.tsx"), /<MockupUpload shop=\{uploadShop\} \/>/);
  assert.match(upload, /api\.addMockupDesign\(batch\.id, file\)/);
  assert.match(upload, /api\.startMockups\(batch\.id, chosen\)/);
  assert.match(upload, /\(templates \?\? \[\]\)\.map\(\(t\) => t\.id\)\.filter\(\(x\) => next\.includes\(x\)\)/);
  assert.match(upload, /className="min-h-\[1\.25rem\] text-xs text-slate-600" translate="no" data-testid="mockup-progress"/);
  assert.equal(runLine({ status: "running", done: 3, total: 10, failed: 0 }), "Making mockups: 3 of 10");
  assert.equal(runLine({ status: "succeeded", done: 9, total: 10, failed: 1 }), "9 mockups made, 1 could not be made");
});

test("nothing says a model draws or changes the design, or promises anything of Etsy", () => {
  const forbidden = /\b(AI|generat\w*|enhanc\w*|redraw\w* automatically|realistic guarantee|approved by Etsy|Etsy-approved)\b/;
  for (const file of [PAGE, UPLOAD, "lib/mockups.ts"]) {
    for (const [, quoted, shown] of read(file).matchAll(/"([^"\n]*)"|>([^<{}\n]+)</g)) {
      const text = quoted ?? shown;
      // "Listings generated" is the allowance's own name; saying mockups do not count toward it is fine.
      assert.doesNotMatch(text.replace("Listings generated", ""), forbidden, `${file}: ${text}`);
    }
  }
  assert.match(read("lib/mockups.ts"), /never redrawn/);
  assert.match(read("lib/mockups.ts"), /Mockups do not count toward Listings generated/);
});

test("templates are in the menu, the page title, the layout test and robots", () => {
  assert.match(read("components/Rail.tsx"), /\{ href: "\/templates", label: "Mockup templates", icon: IconShirt \}/);
  assert.match(read("components/TopStrip.tsx"), /\[\/\^\\\/templates\$\/, "Mockup templates"\]/);
  assert.match(read("e2e/layout.spec.ts"), /"\/templates"/);
  assert.match(read("app/robots.ts"), /"\/templates"/);
  assert.match(read("app/(marketing)/privacy/page.tsx"), /blank garment photos you save as mockup templates/);
});
