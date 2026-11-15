// Listing health's words: each rule is Etsy's, quoted with its page, or labelled as ours; a preview before any
// write; the 50-listing cap; the same verdicts as the free tools.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { checkTags } from "./freeTools/tagCheck.ts";
import { checkTitle } from "./freeTools/titleCheck.ts";
import { approvalSentence, BUCKETS, diffOf, factsLine, FIX_KINDS, fixesFor, isWorking, links, MAIN_BUCKETS, PROBLEMS, rewriteSentence, SELLING_NOTE, withinCap, type HealthProblem } from "./listingHealth.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const sources = readFileSync(new URL("../../docs/guide-sources.md", import.meta.url), "utf8").replace(/\s+/g, " ");
const server = readFileSync(new URL("../../backend/app/pipeline/listing_health.py", import.meta.url), "utf8");
const PAGE = "app/(app)/listing-health/page.tsx";

test("every problem the server can name has words here, and no others", () => {
  const ids = [...server.matchAll(/Problem\("([a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(ids.length >= 12);
  for (const id of ids) assert.ok(PROBLEMS[id], id);
  assert.deepEqual([...new Set(ids)].sort(), Object.keys(PROBLEMS).sort());
});

test("Etsy's rule is quoted from a page we read, with its link; ours says it is ours", () => {
  const kinds = Object.fromEntries([...server.matchAll(/Problem\("([a-z_]+)", (ETSY|ADVICE)/g)].map((m) => [m[1], m[2].toLowerCase()]));
  for (const [id, rule] of Object.entries(PROBLEMS)) {
    if (kinds[id]) assert.equal(rule.kind, kinds[id], `${id}: the server and the page must agree whose rule it is`);
    else assert.ok(server.includes(`Problem("${id}", kind,`), `${id}: whose rule it is depends on what fired`);
    if (rule.kind === "etsy") {
      assert.ok(rule.source.url.startsWith("https://help.etsy.com/"), id);
      assert.ok(sources.includes(rule.quote.replace(/\s+/g, " ")), `${id}: its quote is in docs/guide-sources.md`);
      assert.ok(sources.includes(rule.source.url), `${id}: its page is in docs/guide-sources.md`);
    } else {
      assert.ok(rule.why.length > 10, id);
    }
  }
});

test("nothing on the page reads as a grade, and nothing promises a ranking", () => {
  const page = read(PAGE) + read("lib/listingHealth.ts");
  assert.ok(!/rank higher|more views|more sales|boost|guarantee|SEO score|Etsy score/i.test(page));
  assert.ok(!/\bscore\b|checks met|on average|\d+%|%\s*of/i.test(page.replace(/It does not predict how Etsy ranks your listing/, "")));
  assert.ok(page.includes("It does not predict how Etsy ranks your listing"));
  assert.ok(!/<h1[^>]*>[^<]*Etsy/.test(page));
  assert.deepEqual(MAIN_BUCKETS, ["fix_now", "worth_improving", "selling"]);
  assert.deepEqual(MAIN_BUCKETS.map((b) => BUCKETS.find((x) => x.bucket === b)?.label), ["Fix now", "Worth improving", "Selling"]);
  assert.equal(SELLING_NOTE, "Changing the title or tags of a selling listing can lower its search position.");
  assert.ok(server.includes(`SELLING_NOTE = "${SELLING_NOTE}"`), "the same note on the server");
});

test("a listing is offered only the fixes its own problems ask for", () => {
  const listing = (problems: HealthProblem[]) => ({ problems, suggestions: [] });
  assert.deepEqual(fixesFor(listing([])), []); // a selling listing: nothing
  assert.deepEqual(fixesFor(listing([{ id: "brand", kind: "advice", level: "fix_now", sentence: "", fixes: ["rewrite", "edit"] }])), ["rewrite"]);
  assert.deepEqual(fixesFor(listing([{ id: "tags_missing", kind: "etsy", level: "worth_improving", sentence: "", fixes: ["tags"] },
    { id: "dated_tags", kind: "advice", level: "worth_improving", sentence: "", fixes: ["rewrite", "season_planner"] }])), ["tags", "rewrite"]);
  assert.deepEqual(links({ problems: [{ id: "main_photo", kind: "etsy", level: "fix_now", sentence: "", fixes: ["edit"] }], suggestions: [] }), { edit: true, seasonPlanner: false });
  assert.equal(factsLine({ recent_sales: 4, past_sales: 30, favourites: 1 }, 60), "4 sold in the last 60 days · 1 favourite");
  assert.equal(factsLine({ recent_sales: 0, past_sales: 0, favourites: 0 }, 60), "no sales in the past year");
  const page = read(PAGE);
  assert.ok(page.includes("fixesFor(l).includes(f.kind)"), "the bulk bar offers only what the chosen listings' problems ask for");
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
  assert.equal(rewriteSentence({ rule: "r", free: 2, counted: 1, remaining: 5, fits: true }), "For these: 2 already counted, so free, 1 counted as listing.");
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
  assert.ok(isWorking({ state: "writing" }) && isWorking({ state: "undoing" }) && !isWorking({ state: "applied" }));
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
