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
