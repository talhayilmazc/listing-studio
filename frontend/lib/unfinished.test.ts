// Half-made drafts (components/UnfinishedDrafts.tsx, backend api/unfinished.py): listed with
// their shop and Etsy's reason, finished in one click (the same listing, never a new one),
// a shop that still lacks a setting offered "Set up in <shop>" first.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { finishLine, unfinishedHeading } from "./unfinished.ts";

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
  assert.match(panel, /api\.finishDrafts\(\{ attempt_ids: ready\.map\(\(i\) => i\.attempt_id\) \}\)/);
  assert.match(panel, /`Finish these drafts \(\$\{ready\.length\}\)`/);
  assert.match(panel, /api\.useProfileIn\(item\.profile_id, \{ scope: "shops", connection_ids: \[item\.connection_id\] \}\)/);
  assert.match(panel, /<span>Set up in <\/span><span translate="no">\{i\.shop_name\}<\/span>/);
  assert.match(panel, /nothing new\s+is created and nothing is deleted on Etsy/);
  assert.match(read("app/(app)/batches/[id]/review/page.tsx"), /<UnfinishedDrafts batchId=\{id\} \/>/);
  assert.match(read("app/(app)/dashboard/page.tsx"), /<UnfinishedDrafts \/>/);
  // The review page's shop needs offer the same one click.
  assert.match(read("components/PublishMatrix.tsx"), /<span>Set up in <\/span><span translate="no">\{col\.shop_name \?\? "this shop"\}<\/span>/);
});
