// Half-made drafts (components/UnfinishedDrafts.tsx, backend api/unfinished.py): one list in
// three parts by the server's category (finish these, fix these, delete these on Etsy),
// finished in one click (the same listing, never a new one), a shop that still lacks a
// setting offered "Set up in <shop>" first, nothing ever deleted on Etsy by the app.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import type { UnfinishedDraft } from "./types.ts";
import {
  DELETE_NOTE,
  deleteHeading,
  deleteReason,
  finishLine,
  fixHeading,
  fixReason,
  replacedIds,
  sections,
  unfinishedHeading,
} from "./unfinished.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");

const draft = (over: Partial<UnfinishedDraft>): UnfinishedDraft => ({
  record_id: "r", attempt_id: null, content_id: null, batch_id: null, connection_id: "c", shop_name: "BCT",
  etsy_listing_id: 1, edit_url: "u", title: "t", step: null, error: null, finishing: false, blocked: null,
  profile_id: null, profile_name: null, setup: false, category: "finish", category_label: "Finish now",
  section: "finish", personalization_problems: [], ...over,
});

test("the words", () => {
  assert.equal(unfinishedHeading(1), "1 draft was started on Etsy but not finished");
  assert.equal(unfinishedHeading(3), "3 drafts were started on Etsy but not finished");
  assert.equal(fixHeading(1), "1 draft needs a fix before it can be finished");
  assert.equal(deleteHeading(9), "9 drafts to delete on Etsy");
  assert.equal(finishLine(2, 0), "Finishing 2 drafts: each carries on with the listing Etsy already has.");
  assert.equal(finishLine(0, 1), "1 could not be started yet: the reason is under it.");
  assert.equal(finishLine(0, 0), "Nothing to finish right now.");
});

test("the three parts follow the server's category, never a rule of their own", () => {
  const items = [
    draft({ record_id: "a", section: "finish" }),
    draft({ record_id: "b", section: "fix", category: "fix_personalization", personalization_problems: ["too long"] }),
    draft({ record_id: "c", section: "delete", category: "replaced", replaced_by: 46, replaced_key: "group CDM289" }),
    draft({ record_id: "d", section: "delete", category: "batch_deleted" }),
  ];
  const { finish, fix, remove } = sections(items);
  assert.deepEqual([finish, fix, remove].map((l) => l.map((i) => i.record_id)), [["a"], ["b"], ["c", "d"]]);
  assert.equal(deleteReason(items[2]), "Replaced by listing 46 (matched by group CDM289)");
  assert.match(deleteReason(items[3]), /batch was deleted/);
  assert.match(fixReason(items[1]), /personalization question: too long/);
  assert.match(DELETE_NOTE, /the app never deletes anything on Etsy/);
  assert.equal(replacedIds([{ shop_name: "BCT", etsy_listing_id: 4596864790 }, { shop_name: "BCT", etsy_listing_id: 4596846505 }]),
    "BCT: 4596864790\nBCT: 4596846505");
});

test("finish these drafts, set up in the shop, and where the list is shown", () => {
  const panel = read("components/UnfinishedDrafts.tsx");
  assert.match(panel, /api\.finishDrafts\(\{ record_ids: \(only \? \[only\] : ready\)\.map\(\(i\) => i\.record_id\) \}\)/);
  assert.match(panel, /`Finish these drafts \(\$\{ready\.length\}\)`/);
  // Set up starts the shop's setup at once, for this shop (no dialog: matched or copied).
  assert.match(panel, /startSetup\(\[\{ profileId: item\.profile_id, profileName: item\.profile_name \?\? null, shopIds: \[item\.connection_id\] \}\]\)/);
  assert.doesNotMatch(panel, /ShopSetupDialog/);
  assert.match(panel, /<span>Set up in <\/span><span translate="no">\{i\.shop_name\}<\/span>/);
  assert.match(panel, /nothing new\s+is created and nothing is deleted on Etsy/);
  assert.match(read("app/(app)/batches/[id]/review/page.tsx"), /<UnfinishedDrafts batchId=\{id\} \/>/);
  assert.match(read("app/(app)/dashboard/page.tsx"), /<UnfinishedDrafts \/>/);
});

test("drafts to delete are listed apart with their listing ids, and the app never deletes on Etsy", () => {
  const panel = read("components/UnfinishedDrafts.tsx");
  for (const id of ["replaced-drafts", "replaced-draft", "replaced-copy", "replaced-dismiss", "finish-draft", "fix-drafts"]) {
    assert.ok(panel.includes(`data-testid="${id}"`), id);
  }
  assert.match(panel, /I deleted it on Etsy/);
  assert.match(panel, /i\.can_finish && \(/); // "Finish draft" only where finishing is possible
  assert.match(panel, /i\.can_dismiss && \(/);
  assert.doesNotMatch(panel, /deleteListing|delete_listing|method: "DELETE"/); // nothing is deleted on Etsy
});

test("each listing's list in Where and when says when a title is rewritten for another shop's prefix", () => {
  assert.match(read("lib/whereWhen.ts"), /cell\.note/);
});
