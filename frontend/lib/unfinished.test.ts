// Half-made drafts (components/UnfinishedDrafts.tsx, backend api/unfinished.py): listed with
// their shop and Etsy's reason, finished in one click (the same listing, never a new one),
// a shop that still lacks a setting offered "Set up in <shop>" first.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { REPLACED_NOTE, finishLine, replacedHeading, replacedIds, unfinishedHeading } from "./unfinished.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");

test("the words", () => {
  assert.equal(unfinishedHeading(1), "1 draft was started on Etsy but not finished");
  assert.equal(unfinishedHeading(3), "3 drafts were started on Etsy but not finished");
  assert.equal(finishLine(2, 0), "Finishing 2 drafts: each carries on with the listing Etsy already has.");
  assert.equal(finishLine(0, 1), "1 still needs its shop set up first.");
  assert.equal(finishLine(0, 0), "Nothing to finish right now.");
});

test("finish these drafts, set up in the shop, and where the list is shown", () => {
  const panel = read("components/UnfinishedDrafts.tsx");
  assert.match(panel, /api\.finishDrafts\(\{ attempt_ids: \(only \? \[only\] : ready\)\.map\(\(i\) => i\.attempt_id\) \}\)/);
  assert.match(panel, /`Finish these drafts \(\$\{ready\.length\}\)`/);
  assert.match(panel, /api\.useProfileIn\(item\.profile_id, \{ scope: "shops", connection_ids: \[item\.connection_id\] \}\)/);
  assert.match(panel, /<span>Set up in <\/span><span translate="no">\{i\.shop_name\}<\/span>/);
  assert.match(panel, /nothing new\s+is created and nothing is deleted on Etsy/);
  assert.match(read("app/(app)/batches/[id]/review/page.tsx"), /<UnfinishedDrafts batchId=\{id\} \/>/);
  assert.match(read("app/(app)/dashboard/page.tsx"), /<UnfinishedDrafts \/>/);
  // The review page's shop needs offer the same one click.
  assert.match(read("components/PublishMatrix.tsx"), /<span>Set up in <\/span><span translate="no">\{col\.shop_name \?\? "this shop"\}<\/span>/);
});

test("drafts a newer one replaced are listed apart, with both listing ids, and never deleted on Etsy by the app", () => {
  assert.equal(replacedHeading(1), "1 draft was replaced by a newer draft");
  assert.equal(replacedHeading(9), "9 drafts were replaced by a newer draft");
  assert.match(REPLACED_NOTE, /the app never deletes anything on Etsy/);
  assert.equal(replacedIds([{ shop_name: "BCT", etsy_listing_id: 4596864790 }, { shop_name: "BCT", etsy_listing_id: 4596846505 }]),
    "BCT: 4596864790\nBCT: 4596846505");
  const panel = read("components/UnfinishedDrafts.tsx");
  for (const id of ["replaced-drafts", "replaced-draft", "replaced-copy", "replaced-dismiss", "finish-draft"]) {
    assert.ok(panel.includes(`data-testid="${id}"`), id);
  }
  assert.match(panel, /Replaced by a newer draft/);
  assert.match(panel, /I deleted it on Etsy/);
  assert.match(panel, /i\.can_finish && \(/); // "Finish draft" only where finishing is possible
  assert.doesNotMatch(panel, /deleteListing|delete_listing|method: "DELETE"/); // nothing is deleted on Etsy
});

test("the matrix says when a title is rewritten for another shop's prefix", () => {
  const matrix = read("components/PublishMatrix.tsx");
  assert.match(matrix, /title rewritten once to fit this shop/);
  assert.match(matrix, /title=\{cell\.note \?\? undefined\}/);
});
