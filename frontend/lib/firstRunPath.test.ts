// The sample path writes nothing: the page reads the sample and nothing else, one primary button a step, a preview
// that says nothing was sent, and the seller's next step from their own account.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { afterSample, areaBox, NOTHING_SENT, sampleImage, SAMPLE_STEPS, WRITTEN_AHEAD } from "./firstRunPath.ts";
import { checkTags } from "./freeTools/tagCheck.ts";
import { checkTitle } from "./freeTools/titleCheck.ts";
import { IMAGE_VERSION_FOR_SAMPLE } from "./sampleVersion.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const PAGE = "app/(app)/first-run/page.tsx";
const SERVER = readFileSync(new URL("../../backend/app/pipeline/first_run.py", import.meta.url), "utf8");

test("the path has five steps and ends in the draft", () => {
  assert.deepEqual(SAMPLE_STEPS.map((s) => s.key), ["design", "template", "mockups", "text", "draft"]);
  assert.equal(SAMPLE_STEPS[SAMPLE_STEPS.length - 1].next, null);
});

test("the page writes nothing: the only call it makes reads the sample", () => {
  const page = read(PAGE);
  assert.deepEqual([...page.matchAll(/api\.(\w+)\(/g)].map((m) => m[1]), ["firstRun"]);
  assert.ok(!/fetch\(|method:|approve|publish|createDraft/i.test(page));
  assert.ok(read("lib/api.ts").includes('firstRun: () => req<Sample>("/first-run")'));
});

test("the last step says nothing was sent, and the text says it was written in advance", () => {
  const page = read(PAGE);
  assert.ok(page.includes("{NOTHING_SENT}") && page.includes("{WRITTEN_AHEAD}"));
  assert.ok(NOTHING_SENT.includes("Nothing has gone to Etsy") && WRITTEN_AHEAD.includes("Written in advance"));
  assert.ok(!/<h1/.test(page), "the top strip names the page");
});

test("what comes next depends on the account's own shops, and never sends the sample", () => {
  assert.equal(afterSample(0).href, "/connect");
  assert.equal(afterSample(2).href, "/upload");
  for (const n of [0, 2]) assert.match(afterSample(n).line, /nothing reaches Etsy until you approve it/i);
});

test("the sample's pictures carry the server's version", () => {
  assert.ok(SERVER.includes(`VERSION = "${IMAGE_VERSION_FOR_SAMPLE}"`));
  assert.equal(sampleImage("mockup-white"), `/api/first-run/image?name=mockup-white&v=${IMAGE_VERSION_FOR_SAMPLE}`);
  assert.deepEqual(areaBox([[0.36, 0.26], [0.64, 0.26], [0.64, 0.6], [0.36, 0.6]]), { left: "36%", top: "26%", width: "28%", height: "34%" });
});

test("the sample's text meets every check the page shows it against", () => {
  const title = /"title": "([^"]+)"/.exec(SERVER)?.[1] ?? "";
  const tagBlock = /"tags": \[([\s\S]*?)\],/.exec(SERVER)?.[1] ?? "";
  const tags = [...tagBlock.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.equal(tags.length, 13);
  const open = [...checkTitle(title), ...checkTags(tags, title)].filter((line) => line.verdict === "fail" || line.verdict === "warn");
  assert.deepEqual(open.map((line) => `${line.id}: ${line.detail}`), []);
});

test("the guide on Overview offers the sample, saying it sends nothing", () => {
  const guide = read("components/FirstRunGuide.tsx");
  assert.ok(guide.includes('href="/first-run"') && guide.includes("Nothing goes to Etsy"));
  assert.ok(guide.includes("sampleDoneHere()"));
});
