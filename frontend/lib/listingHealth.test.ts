// Listing health's words: each rule is Etsy's, quoted with its page, or labelled as ours; a preview before any
// write; the 50-listing cap; the same verdicts as the free tools.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { checkTags } from "./freeTools/tagCheck.ts";
import { checkTitle } from "./freeTools/titleCheck.ts";
import { approvalSentence, diffOf, FIX_KINDS, isWorking, RULES, rewriteSentence, scoreTone, toLookAt, withinCap, type HealthListing } from "./listingHealth.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const sources = readFileSync(new URL("../../docs/guide-sources.md", import.meta.url), "utf8").replace(/\s+/g, " ");
const server = readFileSync(new URL("../../backend/app/pipeline/listing_health.py", import.meta.url), "utf8");
const PAGE = "app/(app)/listing-health/page.tsx";

test("every check the server can give has words here, and no others", () => {
  const ids = [...server.matchAll(/Check\("([a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(ids.length >= 20);
  for (const id of ids) assert.ok(RULES[id], id);
  assert.deepEqual([...new Set(ids)].sort(), Object.keys(RULES).sort());
});

test("Etsy's rule is quoted from a page we read, with its link; ours says it is ours", () => {
  const kinds = Object.fromEntries([...server.matchAll(/Check\("([a-z_]+)", (ETSY|ADVICE)/g)].map((m) => [m[1], m[2].toLowerCase()]));
  for (const [id, rule] of Object.entries(RULES)) {
    assert.equal(rule.kind, kinds[id], `${id}: the server and the page must agree whose rule it is`);
    if (rule.kind === "etsy") {
      assert.ok(rule.source.url.startsWith("https://"), id);
      assert.ok(sources.includes(rule.quote.replace(/\s+/g, " ")) || read("lib/freeTools/titleCheck.ts").includes(rule.quote) || read("lib/freeTools/tagCheck.ts").includes(rule.quote), id);
    } else {
      assert.ok(rule.why.length > 10, id);
    }
  }
  assert.ok(sources.includes(RULES.attributes.kind === "etsy" ? RULES.attributes.quote : ""), "the attributes quote is in guide-sources");
});

test("the page promises no ranking, and shows the scope of its figure", () => {
  const page = read(PAGE) + read("lib/listingHealth.ts");
  assert.ok(!/rank higher|more views|more sales|boost|guarantee|SEO score|Etsy score/i.test(page));
  assert.ok(page.includes("It does not predict how Etsy ranks your listing"));
  assert.ok(!/<h1[^>]*>[^<]*Etsy/.test(page));
});

test("the page writes only from Approve and Undo, and each listing links back to Etsy", () => {
  const page = read(PAGE);
  assert.equal(page.match(/api\.listingFixApprove\(/g)?.length, 1);
  assert.ok(page.includes('data-testid="health-approve"') && page.includes("api.listingFixUndo(["));
  assert.ok(page.includes("href={listing.url}") && page.includes("href={fix.url}"));
  const api = read("lib/api.ts");
  assert.ok(api.includes('confirm: true'), "the approval says so to the server");
});

test("a rewrite says the rule before it runs", () => {
  const page = read(PAGE);
  assert.ok(page.includes('if (kind !== "rewrite") return preview(ids, kind);'), "the other two preview at once; a rewrite asks first");
  assert.ok(page.includes("ask.check.rule") && page.includes("rewriteSentence(ask.check)"));
  assert.equal(page.match(/preview\(ask\.ids, "rewrite", true\)/g)?.length, 1, "accept_rewrite is sent from the question only");
  assert.ok(FIX_KINDS.find((f) => f.kind === "rewrite")?.about.includes("allowance"));
  assert.equal(rewriteSentence({ rule: "r", free: 2, counted: 1, remaining: 5, fits: true }), "For these: 2 free, 1 counted as listing generated.");
  assert.ok(rewriteSentence({ rule: "r", free: 0, counted: 9, remaining: 5, fits: false }).includes("will not run"));
});

test("at most 50 in one approval, and the rest are said", () => {
  const ids = Array.from({ length: 60 }, (_, i) => i);
  const kept = withinCap([...ids, 1, 2], 50);
  assert.equal(kept.ids.length, 50);
  assert.equal(kept.left, 10);
  assert.ok(approvalSentence(50, 7).includes("50 live listings") && approvalSentence(1, 7).includes("1 live listing "));
  assert.equal(approvalSentence(0, 7), "Tick the changes you want. Nothing has gone to Etsy.");
});

test("the diff shows what goes and what comes, and nothing that stays the same as a change", () => {
  const lines = diffOf({
    before: { title: "Old", tags: ["keep", "drop"], attributes: [{ property_id: 2, property_name: "Sleeve", value_ids: [], values: [] }] },
    after: { title: "New", tags: ["KEEP", "fresh"], attributes: [{ property_id: 2, property_name: "Sleeve", value_ids: [9], values: ["Short"] }] },
  });
  assert.deepEqual(lines[0], { what: "Title", removed: ["Old"], added: ["New"], kept: [] });
  assert.deepEqual(lines[1], { what: "Tags", removed: ["drop"], added: ["fresh"], kept: ["keep"] });
  assert.deepEqual(lines[2], { what: "Sleeve", removed: [], added: ["Short"], kept: [] });
  assert.deepEqual(diffOf({ before: { title: "Same", tags: ["a"] }, after: { title: "Same", tags: ["A"] } }), []);
});

test("small things", () => {
  assert.equal(scoreTone(95), "done");
  assert.equal(scoreTone(75), "attention");
  assert.equal(scoreTone(40), "failed");
  assert.ok(isWorking({ state: "writing" }) && isWorking({ state: "undoing" }) && !isWorking({ state: "applied" }));
  const row = { checks: [{ id: "a", kind: "etsy", verdict: "fail", detail: "" }, { id: "b", kind: "advice", verdict: "note", detail: "" }] } as HealthListing;
  assert.deepEqual(toLookAt(row).map((c) => c.id), ["a"]);
});

// The same answers as the free tools, from the same shared cases: backend/scripts/free_tool_cases.py writes the
// verdicts of pipeline/listing_health.py, and tests/test_free_tool_rules.py checks they are still its answers.
const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/free_tool_cases.json", import.meta.url), "utf8"));

for (const c of cases.titles) {
  test(`same verdicts as the title checker: ${c.text}`, () => {
    const tool = Object.fromEntries(checkTitle(c.text).map((l) => [l.id, l.verdict]));
    assert.deepEqual(tool, c.health);
  });
}

for (const c of cases.tag_sets) {
  test(`same verdicts as the tag checker: ${c.tags.slice(0, 3).join(", ")}`, () => {
    const tool = Object.fromEntries(checkTags(c.tags, c.title).map((l) => [l.id, l.verdict]));
    // With no title typed, the tool asks for one. A live listing always has one.
    assert.deepEqual(c.title ? tool : { ...tool, title_words: c.health.title_words }, c.health);
  });
}
