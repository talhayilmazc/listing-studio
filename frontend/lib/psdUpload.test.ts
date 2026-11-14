// Uploading a Photoshop mockup (feature/psd-templates): file checks, pieces, progress, the words.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { MAX_BYTES, NOT_PSD, RIGHTS, TOO_BIG, fileProblem, percent, pieces, sameFile, sizeLine, statusLine } from "./psdUpload.ts";

const MB = 1024 * 1024;

test("only .psd and .psb up to 500 MB", () => {
  assert.equal(fileProblem({ name: "Tee mockup.PSD", size: 120 * MB }), null);
  assert.equal(fileProblem({ name: "big.psb", size: MAX_BYTES }), null);
  assert.equal(fileProblem({ name: "big.psb", size: MAX_BYTES + 1 }), TOO_BIG);
  assert.equal(fileProblem({ name: "photo.png", size: 10 }), NOT_PSD);
});

test("the pieces start where the server's copy ends", () => {
  assert.deepEqual(pieces(0, 40 * MB, 16 * MB), [
    { offset: 0, end: 16 * MB }, { offset: 16 * MB, end: 32 * MB }, { offset: 32 * MB, end: 40 * MB }]);
  // After a dropped connection the server had 20 MB: the rest from there, nothing sent twice.
  assert.deepEqual(pieces(20 * MB, 40 * MB, 16 * MB), [{ offset: 20 * MB, end: 36 * MB }, { offset: 36 * MB, end: 40 * MB }]);
  assert.deepEqual(pieces(40 * MB, 40 * MB, 16 * MB), []);
});

test("progress is real and never says 100 before the end", () => {
  assert.equal(percent(0, 100), 0);
  assert.equal(percent(999, 1000), 99);
  assert.equal(percent(1000, 1000), 100);
  assert.equal(sizeLine(120 * MB, 270 * MB), "120 of 270 MB");
  assert.equal(statusLine("receiving", 135 * MB, 270 * MB), "Uploading: 50% · 135 of 270 MB");
  assert.equal(statusLine("converting", 0, 1), "Reading the layers and the smart object…");
});

test("an unfinished upload is continued with the same file only", () => {
  assert.ok(sameFile({ filename: "Tee.psd", size: 5 }, { name: "Tee.psd", size: 5 }));
  assert.ok(!sameFile({ filename: "Tee.psd", size: 5 }, { name: "Tee.psd", size: 6 }));
});

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the seller confirms the right to use it, in the server's words", () => {
  const server = read("../../backend/app/api/psd_templates.py");
  assert.ok(server.includes(`RIGHTS_STATEMENT = "${RIGHTS}"`));
  assert.equal(RIGHTS, "I have the right to use this mockup for my listings.");
  for (const [name, text] of [["TOO_BIG", TOO_BIG], ["NOT_PSD", NOT_PSD]] as const) {
    assert.ok(server.replace(/"\s*\n\s*"/g, "").includes(text), `${name} matches the server`);
  }
});

test("the templates page offers it next to the photo upload, and a PSD photo opens its preview", () => {
  const page = read("../app/(app)/templates/page.tsx");
  assert.ok(page.includes("<PsdUpload onDone={load}"));
  assert.ok(page.includes("setPsdFocus(psd.upload_id)"));
  const panel = read("../components/PsdUpload.tsx");
  assert.ok(panel.includes("Use this template") && panel.includes("psd-rights"));
});
