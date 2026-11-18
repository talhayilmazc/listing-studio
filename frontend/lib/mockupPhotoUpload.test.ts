// Sending a blank photo into a mockup template: pieces over 8 MB, "Processing…" instead of a row stuck at 100%,
// "Couldn't upload <file>. Try again" for that file only, two at a time; and "This photo has one product".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ONE_PRODUCT, offersOneProduct } from "./mockupOneProduct.ts";
import {
  ANSWER_WAIT_MS, PHOTOS_AT_ONCE, PIECE_BYTES, PIECE_TRIES, UploadError, couldNot, inPieces, newUploadId, pieces, progressLine,
  sendPhoto, worthAnother,
} from "./mockupPhotoUpload.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the row never says 100%: a percentage while the bytes go, then Processing…", () => {
  assert.equal(progressLine("sending", 0), "Uploading 0%");
  assert.equal(progressLine("sending", 41.9), "Uploading 41%");
  assert.equal(progressLine("sending", 100), "Uploading 99%");
  assert.equal(progressLine("processing", 100), "Processing…");
  assert.equal(couldNot("shirt black.jpg"), "Couldn't upload shirt black.jpg. Try again");
});

test("a photo over 8 MB goes in 8 MB pieces, from where the server says the file ends", () => {
  assert.equal(PIECE_BYTES, 8 * 1024 * 1024);
  assert.equal(inPieces(PIECE_BYTES), false);
  assert.equal(inPieces(PIECE_BYTES + 1), true);
  assert.deepEqual(pieces(0, 20, 8), [{ offset: 0, end: 8 }, { offset: 8, end: 16 }, { offset: 16, end: 20 }]);
  assert.deepEqual(pieces(16, 20, 8), [{ offset: 16, end: 20 }]);
  assert.deepEqual(pieces(20, 20, 8), []);
  assert.equal(PHOTOS_AT_ONCE, 2);
  assert.equal(ANSWER_WAIT_MS, 60_000);
  assert.ok(PIECE_TRIES >= 3);
  for (const status of [0, 408, 429, 502, 503, 504]) assert.ok(worthAnother(status), String(status));
  for (const status of [400, 404, 409, 413, 415, 422]) assert.ok(!worthAnother(status), String(status));
  assert.match(newUploadId(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.notEqual(newUploadId(), newUploadId());
});

/** A stand-in for XMLHttpRequest: each request is handed to `answer`, which says what the server does. */
type Call = { method: string; url: string; size: number };
function fakeServer(answer: (call: Call, n: number) => { status: number; body?: unknown } | "silence" | "dropped") {
  const calls: Call[] = [];
  class FakeXHR {
    upload: { onprogress?: (e: { loaded: number; total: number }) => void; onload?: () => void } = {};
    onload?: () => void;
    onerror?: () => void;
    onabort?: () => void;
    status = 0;
    statusText = "";
    responseText = "";
    private method = "";
    private url = "";
    private aborted = false;
    open(method: string, url: string) { this.method = method; this.url = url; }
    setRequestHeader() {}
    abort() { this.aborted = true; queueMicrotask(() => this.onabort?.()); }
    send(body: unknown) {
      const size = body instanceof Blob ? body.size : typeof body === "string" ? body.length : 1;
      const call = { method: this.method, url: this.url, size };
      calls.push(call);
      const what = answer(call, calls.length);
      setTimeout(() => {
        if (this.aborted) return;
        if (what === "dropped") return this.onerror?.();
        this.upload.onprogress?.({ loaded: size, total: size });
        this.upload.onload?.();
        if (what === "silence") return;  // the bytes left; no answer ever comes
        this.status = what.status;
        this.responseText = JSON.stringify(what.body ?? {});
        this.onload?.();
      }, 1);
    }
  }
  (globalThis as { XMLHttpRequest?: unknown }).XMLHttpRequest = FakeXHR;
  return calls;
}

const file = (size: number, name = "navy tee.png") => new File([new Uint8Array(size)], name, { type: "image/png" });
const quick = { wait: 40, gap: () => 1 };

test("a small photo is one request; once its bytes are sent the row says Processing…", async () => {
  const calls = fakeServer(() => ({ status: 201, body: { id: "p1" } }));
  const seen: string[] = [];
  const photo = await sendPhoto("t1", file(5), "Navy", "u-1", (phase, pct) => seen.push(progressLine(phase, pct)), { piece: 10, ...quick });
  assert.equal(photo.id, "p1");
  assert.deepEqual(calls.map((c) => `${c.method} ${c.url}`), ["POST /api/mockup-templates/t1/photos"]);
  assert.equal(seen[0], "Uploading 0%");
  assert.equal(seen.at(-1), "Processing…");
  assert.ok(!seen.includes("Uploading 100%"));
});

test("a large photo goes in pieces: one that fails is sent again by itself, a gap goes on from what the server holds", async () => {
  let held = 0;
  const calls = fakeServer((call, n) => {
    if (call.method === "POST") return { status: 201, body: { id: "p2" } };
    const offset = Number(new URLSearchParams(call.url.split("?")[1]).get("offset"));
    if (n === 2) return { status: 503 };                    // the second request: a busy proxy
    if (n === 4) return "dropped";                          // the connection dropped once
    if (offset > held) return { status: 409, body: { detail: { message: "A piece is missing before this one.", received: held } } };
    held = Math.max(held, offset + call.size);
    return { status: 200, body: { received: held, size: 25 } };
  });
  const seen: string[] = [];
  const photo = await sendPhoto("t1", file(25), "Navy", "u-2", (phase, pct) => seen.push(progressLine(phase, pct)), { piece: 10, ...quick });
  assert.equal(photo.id, "p2");
  const puts = calls.filter((c) => c.method === "PUT").map((c) => new URLSearchParams(c.url.split("?")[1]).get("offset"));
  assert.deepEqual(puts, ["0", "10", "10", "20", "20"]);  // each failed piece again, by itself; nothing sent twice otherwise
  assert.ok(calls.every((c) => c.method !== "PUT" || c.url.includes("upload=u-2") && c.url.includes("size=25")));
  assert.equal(calls.at(-1)!.method + " " + calls.at(-1)!.url, "POST /api/mockup-templates/t1/photos/assembled");
  assert.equal(seen.at(-1), "Processing…");
});

test("no answer after the last byte ends the wait; a refusal is the server's own words; neither is sent again by itself", async () => {
  let calls = fakeServer(() => "silence");
  await assert.rejects(sendPhoto("t1", file(5), "Navy", "u-3", () => {}, { piece: 10, ...quick }),
    (e: UploadError) => e instanceof UploadError && e.status === 0);
  assert.equal(calls.length, 1);
  calls = fakeServer(() => ({ status: 415, body: { detail: "This file is not an image we can read." } }));
  await assert.rejects(sendPhoto("t1", file(5), "Navy", "u-4", () => {}, { piece: 10, ...quick }),
    (e: UploadError) => e.status === 415 && e.message === "This file is not an image we can read.");
  assert.equal(calls.length, 1);
  // A piece the server keeps refusing stops after its tries: the page then says "Couldn't upload … Try again".
  calls = fakeServer(() => ({ status: 503 }));
  await assert.rejects(sendPhoto("t1", file(25), "Navy", "u-5", () => {}, { piece: 10, ...quick }), (e: UploadError) => e.status === 503);
  assert.equal(calls.length, PIECE_TRIES);
});

test("the page: two at a time, Processing…, and Try again sends that file only", () => {
  const page = read("../app/(app)/mockups/templates/page.tsx");
  assert.ok(page.includes("await runLimited(sendable, PHOTOS_AT_ONCE, async (row, i) => {"));
  assert.ok(page.includes("sendPhoto(templateId, row.file, row.name, row.upload, (phase, pct) => update(row.key, { phase, pct }))"));
  assert.ok(page.includes('progressLine(r.phase ?? "sending", r.pct)'), "never a bare percentage that can read 100%");
  assert.ok(page.includes('data-testid="template-row-again"') && page.includes("{couldNot(r.file.name)}"));
  assert.match(page, /async function again\(row: Row\) \{[\s\S]*?const id = await sendRow\(templateId, row\);/);
  assert.ok(!page.includes("api.addMockupPhoto("), "the one-request-per-file upload with a 3-minute wait is gone");
});

test("This photo has one product: offered where it changes something", () => {
  assert.equal(ONE_PRODUCT, "This photo has one product");
  assert.ok(offersOneProduct({ status: "ready", areas: [{}, {}], layout: "several" }));
  assert.ok(offersOneProduct({ status: "ready", areas: [{}], layout: "front_back" }));
  assert.ok(!offersOneProduct({ status: "ready", areas: [{}], layout: "single" }));
  assert.ok(!offersOneProduct({ status: "preparing", areas: [{}, {}], layout: "several" }));
  assert.ok(!offersOneProduct({ status: "ready", psd: {}, areas: [{}, {}], layout: "several" }));
  const page = read("../app/(app)/mockups/templates/page.tsx");
  const editor = read("../components/PrintAreaEditor.tsx");
  assert.ok(page.includes('data-testid="template-photo-one-product"') && editor.includes('data-testid="editor-one-product"'));
});
