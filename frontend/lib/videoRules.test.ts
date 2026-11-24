// A listing's video (feature/listing-video): the page's copy of Etsy's rules against the cases the server reads
// too, the slot's words, and that a listing without a video changes nothing on the page but one quiet line.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ACCEPT, EXTENSIONS, MAX_BYTES, MAX_SECONDS, MIN_SECONDS, VIDEO_RE, busy, draftLine, durationLabel, durationProblem, failedLine,
  fileProblem, slotOf, uploadingLine, videoLine, type ListingVideo, type VideoDraft,
} from "./videoRules.ts";
import { pieces } from "./psdUpload.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/video_rule_cases.json", import.meta.url), "utf8"));

const video = (over: Partial<ListingVideo> = {}): ListingVideo => ({
  id: "v1", group_key: "BR100", connection_id: null, filename: "clip.mp4", size: 5_000_000, received: 5_000_000, chunk_size: 8 * 1024 * 1024,
  status: "ready", error: null, seconds: 8, duration: "0:08", width: 1080, height: 1920, poster_url: "/api/listing-videos/v1/poster?v=a", kept: true, ...over,
});
const draft = (over: Partial<VideoDraft> = {}): VideoDraft => ({
  content_id: "c1", group_key: "BR100", connection_id: "s1", shop_name: "Pine Ridge Tees", video_state: null, video_error: null, video_id: null, ...over,
});

test("the page and the server say the same of every file and every length", () => {
  for (const c of cases.files) assert.equal(fileProblem({ name: c.name, size: c.size }), c.problem, JSON.stringify(c));
  for (const c of cases.durations) {
    assert.equal(durationProblem(c.seconds), c.problem, JSON.stringify(c));
    assert.equal(durationLabel(c.seconds), c.label, JSON.stringify(c));
  }
  assert.equal(durationProblem(22), "This video is 22 s; Etsy takes 5–15 s.");
  assert.deepEqual([MIN_SECONDS, MAX_SECONDS, MAX_BYTES, EXTENSIONS], [5, 15, 100 * 1024 * 1024, [".mp4", ".mov"]]);
  // The same limits as the server's one place for them.
  const rules = read("../backend/app/pipeline/video_rules.py");
  assert.match(rules, /MIN_SECONDS = 5\.0\nMAX_SECONDS = 15\.0/);
  assert.match(rules, /MAX_BYTES = 100 \* 1024 \* 1024\nEXTENSIONS = \("\.mp4", "\.mov"\)/);
  assert.ok(VIDEO_RE.test("Clip.MOV") && VIDEO_RE.test("a.mp4") && !VIDEO_RE.test("a.mp4.jpg") && !VIDEO_RE.test("a.avi"));
  assert.match(ACCEPT, /\.mp4,\.mov/);
});

test("the slot shows the listing's video, the one on its way, and each shop's own", () => {
  const all = [video(), video({ id: "v2", status: "failed", error: "This video is 22 s; Etsy takes 5–15 s." }),
    video({ id: "v3", connection_id: "s2", filename: "shop.mov", duration: "0:06" }), video({ id: "v4", group_key: "BR200" })];
  assert.deepEqual([slotOf(all, "BR100", null).current?.id, slotOf(all, "BR100", null).pending?.id], ["v1", "v2"]);
  assert.deepEqual([slotOf(all, "BR100", "s2").current?.id, slotOf(all, "BR100", "s2").pending], ["v3", null]);
  assert.equal(slotOf(all, "BR300", null).current, null);
  assert.equal(videoLine(all[0]), "0:08 · clip.mp4");
  assert.equal(busy([video({ status: "processing" })], [], "BR100"), true);
  assert.equal(busy([video()], [draft({ video_state: "sending" })], "BR100"), true);
  assert.equal(busy([video()], [draft({ video_state: "attached" })], "BR100"), false);
});

test("a draft says what became of its video, in the brief's words when Etsy did not take it", () => {
  assert.equal(draftLine(draft({ video_state: "attached" }), false), "Video attached to the draft.");
  assert.equal(draftLine(draft({ video_state: "failed", video_error: "Etsy said the video is too short" }), false),
    "Draft made; the video couldn't be added: Etsy said the video is too short");
  assert.equal(draftLine(draft({ video_state: "failed", video_error: "too short" }), true), "Draft made in Pine Ridge Tees; the video couldn't be added: too short");
  assert.equal(draftLine(draft(), true), "The draft in Pine Ridge Tees has no video yet.");
  assert.equal(draftLine(draft({ video_state: "sending" }), false), "Sending the video to the draft…");
});

test("a video goes up in the server's 8 MB pieces and a failed one says so with Try again", () => {
  const eight = 8 * 1024 * 1024;
  assert.deepEqual(pieces(0, 20 * 1024 * 1024, eight).map((p) => p.end - p.offset), [eight, eight, 4 * 1024 * 1024]);
  assert.deepEqual(pieces(eight, 20 * 1024 * 1024, eight)[0].offset, eight);  // goes on from where the server has it
  assert.equal(uploadingLine(4, 10), "Uploading 40%");
  assert.equal(failedLine("clip.mp4", { status: 0, message: "The upload could not reach the server." }), "Couldn't upload clip.mp4. Try again.");
  assert.equal(failedLine("clip.mp4", { status: 413, message: "This video is 130 MB; Etsy takes videos up to 100 MB." }),
    "This video is 130 MB; Etsy takes videos up to 100 MB.");
  const slot = read("components/VideoSlot.tsx");
  assert.match(slot, /Processing…/);
  assert.match(slot, /Try again/);
  // Sending to Etsy is always the seller's click, confirmed; replacing asks first.
  assert.match(slot, /confirm: true/);
  assert.match(slot, /data-testid="video-confirm"/);
});

test("the slot is on the review card, the batch row and the shop's row; a video is never among the photos", () => {
  assert.match(read("components/ReviewCard.tsx"), /<VideoSlot batchId=\{batchId\} groupKey=\{initial\.group_key \?\? ""\} contentId=\{initial\.id\} \/>/);
  assert.match(read("app/(app)/batches/[id]/page.tsx"), /<VideoSlot batchId=\{id\} groupKey=\{g\.key\} contentId=\{contentOf\(g\)\} \/>/);
  assert.match(read("components/ShopWorkspace.tsx"), /<VideoSlot batchId=\{batchId\} groupKey=\{row\.group_key\} contentId=\{row\.content_id\} shop=/);
  const upload = read("app/(app)/upload/page.tsx");
  assert.match(upload, /const videos = items\.filter\(\(it\) => VIDEO_RE\.test\(it\.file\.name\)\);/);
  assert.match(upload, /const others = items\.length - images\.length - zips\.length - videos\.length;/);
  // The photos' own rule is untouched: a video's name is not an image's.
  assert.ok(!/\.(mp4|mov)/i.test(read("lib/staging.ts")) && !/\.(mp4|mov)/i.test(read("lib/grouping.ts")));
});
