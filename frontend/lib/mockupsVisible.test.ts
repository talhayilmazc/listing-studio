// Mockups in the menu, old addresses kept, Home's shortcuts (feature/mockups-visible).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { MOVED, movedTo } from "./oldAddresses.ts";
import { SHORTCUTS, mockupsCard } from "./homeShortcuts.ts";
import { tabsFor } from "./sectionTabs.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the old addresses send the seller to the new ones, query and all", () => {
  assert.equal(movedTo("/templates", {}), "/mockups/templates");
  assert.equal(movedTo("/templates/designs", { run: "r1", design: "d9" }), "/mockups?run=r1&design=d9");
  assert.equal(movedTo("/templates/designs", { templates: "t1,t2" }), "/mockups?templates=t1%2Ct2");
  for (const [from, to] of Object.entries(MOVED)) {
    const folder = from.replace(/^\//, "");
    const page = read(`../app/(app)/${folder}/page.tsx`);
    assert.ok(page.includes(`movedTo("${from}"`) && page.includes("redirect("), from);
    assert.ok(tabsFor(to), `${to} is a Mockups tab`);
  }
});

test("Home: make mockups with a template, add the first one without", () => {
  assert.equal(mockupsCard(null), null);
  assert.equal(mockupsCard(0)?.href, "/mockups/templates");
  assert.match(mockupsCard(0)!.line, /Photoshop mockup \(PSD\)/);
  assert.equal(mockupsCard(3)?.href, "/mockups");
  assert.match(mockupsCard(3)!.line, /3 templates/);
  assert.deepEqual(SHORTCUTS.map((s) => s.href), ["/listing-health", "/season-planner", "/profiles"]);
  const home = read("../app/(app)/dashboard/page.tsx");
  assert.ok(home.includes('{fresh ? null : <HomeShortcuts key="shortcuts" />}'));
});

test("nothing links to the old addresses any more", () => {
  for (const file of ["../components/MockupUpload.tsx", "./mockups.ts", "./mockupRuns.ts", "../components/Rail.tsx", "./sectionTabs.ts"]) {
    assert.doesNotMatch(read(file), /["`]\/templates(\/designs)?[?"`]/, file);
  }
});

import { DEFAULT_OPTIONS, making, selectedLabel, zipLine } from "./mockupDownloads.ts";

test("downloads: the defaults, the ZIP's line and progress, the selection's button", () => {
  assert.deepEqual(DEFAULT_OPTIONS, { format: "jpg", size: "original", quality: 92, naming: "full", layout: "folders" });
  assert.equal(zipLine({ status: "building", total: 20, done: 5, files: null, error: null }), "Making the ZIP: 5 of 20");
  assert.equal(zipLine({ status: "ready", total: 20, done: 20, files: 20, error: null }, "12 MB"), "20 files, 12 MB. Kept 48 hours, then deleted.");
  assert.ok(making({ status: "queued" }) && !making({ status: "ready" }));
  assert.equal(selectedLabel(3), "Download selected (3)");
  const page = read("../app/(app)/mockups/downloads/page.tsx");
  assert.ok(page.includes("api.mockupZips()") && page.includes('data-testid="download-zip"'));
  assert.ok(!/google|drive/i.test(page.replace(/Google Drive, a folder/g, "")), "the app uploads nowhere");
  const editor = read("../components/DesignEditor.tsx");
  assert.ok(editor.includes('data-testid="design-editor-download"'));
});

import { SHARPEN_LABELS, keptLine } from "./mockupDownloads.ts";

test("the editor: sharpening on the run, kept mockups said, presets, the selection, Centre", () => {
  assert.deepEqual(SHARPEN_LABELS.map(([v]) => v), ["off", "low", "medium"]);
  assert.equal(keptLine(0), null);
  assert.equal(keptLine(3), "3 mockups were kept as they were: nothing that shows in them had changed.");
  const editor = read("../components/DesignEditor.tsx");
  for (const id of ["design-editor-centre", "design-editor-presets", "design-editor-preset-all", "design-editor-preset-save",
    "design-editor-presets-usual", "selectedScopeLabel(selected.length)"]) { // the scope buttons are built from APPLY_SCOPES
    assert.ok(editor.includes(id), id);
  }
});
