// Page weight and uploads (2026-10-23): three files at a time with retries, identical requests
// shared, small versioned image copies, and images below the fold loaded only when reached.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { UPLOAD_PARALLEL, UPLOAD_TRIES, backoff, retryable, runLimited, withRetry } from "./uploadQueue.ts";
import { shareInFlight } from "./inFlight.ts";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const read = (path: string) => readFileSync(join(root, path), "utf8");
const err = (status: number) => Object.assign(new Error(`status ${status}`), { status });

test("at most three files go at once, and every file is sent", async () => {
  let going = 0;
  let most = 0;
  const done: number[] = [];
  await runLimited([1, 2, 3, 4, 5, 6, 7, 8], UPLOAD_PARALLEL, async (n) => {
    going += 1;
    most = Math.max(most, going);
    await new Promise((r) => setTimeout(r, 5 + (n % 3) * 5));
    done.push(n);
    going -= 1;
  });
  assert.equal(UPLOAD_PARALLEL, 3);
  assert.equal(most, 3);
  assert.deepEqual(done.sort((a, b) => a - b), [1, 2, 3, 4, 5, 6, 7, 8]);
  await runLimited([], 3, async () => assert.fail("nothing to send"));
});

test("a file that fails on the way is tried again by itself; a refusal is not", async () => {
  for (const status of [0, 408, 429, 500, 502, 503, 504]) assert.ok(retryable(err(status)), String(status));
  for (const status of [400, 401, 403, 404, 409, 413, 415, 422]) assert.ok(!retryable(err(status)), String(status));
  assert.ok(!retryable(new Error("no status")));

  let calls = 0;
  const told: number[] = [];
  const answer = await withRetry(async () => {
    calls += 1;
    if (calls < 3) throw err(502);
    return "stored";
  }, { onRetry: (n) => told.push(n), wait: () => 0 });
  assert.equal(answer, "stored");
  assert.equal(calls, 3);
  assert.deepEqual(told, [2, 3]);

  let refused = 0;
  await assert.rejects(withRetry(async () => { refused += 1; throw err(415); }, { wait: () => 0 }), /415/);
  assert.equal(refused, 1);

  let gone = 0;
  await assert.rejects(withRetry(async () => { gone += 1; throw err(0); }, { wait: () => 0 }), /status 0/);
  assert.equal(gone, UPLOAD_TRIES);
  assert.deepEqual([backoff(2), backoff(3), backoff(4)], [1000, 3000, 9000]);
});

test("files are sent as chosen: nothing in the browser resizes or recompresses them", () => {
  for (const file of ["lib/uploadQueue.ts", "lib/api.ts", "components/MockupUpload.tsx", "app/(app)/upload/page.tsx", "app/(app)/templates/page.tsx"]) {
    const src = read(file);
    assert.ok(!/toBlob\(|toDataURL\(|createImageBitmap\(|OffscreenCanvas/.test(src), file);
  }
  // Every upload goes through the queue: no page sends its files one by one any more.
  for (const file of ["components/MockupUpload.tsx", "app/(app)/upload/page.tsx", "app/(app)/templates/page.tsx"]) {
    const src = read(file);
    assert.ok(src.includes("runLimited(") && src.includes("withRetry("), file);
  }
  assert.ok(read("app/(app)/upload/page.tsx").includes('data-testid="upload-retry-failed"'));
});

test("identical requests at the same moment share one; the next ask is a new one", async () => {
  const shared = shareInFlight<string>();
  let started = 0;
  const run = () => { const n = (started += 1); return new Promise<string>((r) => setTimeout(() => r(`answer ${n}`), 5)); };
  const [a, b, c] = await Promise.all([shared("/api/profiles", run), shared("/api/profiles", run), shared("/api/quota", run)]);
  assert.equal(started, 2);
  assert.equal(a, b);
  assert.notEqual(a, c);
  await shared("/api/profiles", run);
  assert.equal(started, 3, "nothing is kept once it has answered");
  // A failure is shared too, then forgotten.
  const failing = shareInFlight<string>();
  let tries = 0;
  const boom = () => { tries += 1; return Promise.reject(new Error("down")); };
  await Promise.all([assert.rejects(failing("/x", boom)), assert.rejects(failing("/x", boom))]);
  await assert.rejects(failing("/x", boom));
  assert.equal(tries, 2);
  // Only GETs, and each caller parses its own copy.
  const api = read("lib/api.ts");
  assert.match(api, /\(init\?\.method \?\? "GET"\)\.toUpperCase\(\) === "GET"/);
  assert.match(api, /JSON\.parse\(text\)/);
});

test("image URLs carry a size and a version, and the version is the server's", () => {
  const api = read("lib/api.ts");
  const backend = readFileSync(join(root, "..", "backend", "app", "pipeline", "thumbs.py"), "utf8");
  const front = api.match(/export const IMAGE_VERSION = (\d+);/)?.[1];
  const back = backend.match(/^VERSION = (\d+)$/m)?.[1];
  assert.ok(front && front === back, `IMAGE_VERSION ${front} must be thumbs.VERSION ${back}`);
  assert.match(api, /export type ImageSize = "small" \| "thumb" \| "medium";/);
  for (const sizes of ['"small": 240', '"thumb": 480', '"medium": 1200']) assert.ok(backend.includes(sizes), sizes);
  assert.match(api, /assetImage: \(id: string, size\?: ImageSize[^]*?v: IMAGE_VERSION/);
  assert.match(api, /mockupPhotoImageUrl: \(id: string, size\?: ImageSize\) =>[^]*?v: IMAGE_VERSION/);
  assert.match(api, /mockupOutputImageUrl: [^]*?v: stamp/);
  assert.match(api, /mockupDesignFileUrl: [^]*?v: stamp/);
});

function sources(dir: string): string[] {
  return readdirSync(join(root, dir)).flatMap((name) => {
    const path = `${dir}/${name}`;
    if (statSync(join(root, path)).isDirectory()) return name === "node_modules" ? [] : sources(path);
    return /\.tsx$/.test(name) ? [path] : [];
  });
}

/** Whole-photo views where the image is the point and on screen at once: they load at once. */
const VIEWERS = new Set(["components/CoverCropper.tsx", "components/DesignEditor.tsx", "components/PhotoViewer.tsx", "components/PrintAreaEditor.tsx"]);

test("every screen asks for a named copy, never a width or the full file, and grids load lazily", () => {
  const files = [...sources("components"), ...sources("app")];
  const imageCall = /api\.(assetImage|mockupPhotoImageUrl|mockupOutputImageUrl)\(([^()]|\([^()]*\))*\)/g;
  let checked = 0;
  for (const file of files) {
    const src = read(file);
    for (const m of src.matchAll(imageCall)) {
      checked += 1;
      assert.ok(!/,\s*\d{2,4}\b/.test(m[0]), `${file}: ${m[0]} asks for a pixel width`);
      assert.ok(/"(small|thumb|medium)"/.test(m[0]), `${file}: ${m[0]} asks for the full file (downloads only)`);
      if (VIEWERS.has(file)) assert.ok(/"medium"|"small"/.test(m[0]), `${file}: ${m[0]}`);
    }
    if (VIEWERS.has(file)) continue;
    // Each <img> showing one of our images is lazy.
    for (const tag of src.matchAll(/<img\b[^>]*?(\/>|>)/gs)) {
      if (!/api\.(assetImage|mockupPhotoImageUrl|mockupOutputImageUrl)|thumbnail_url|preview/.test(tag[0])) continue;
      assert.ok(/loading="lazy"/.test(tag[0]), `${file}: an image without loading="lazy": ${tag[0].slice(0, 120)}`);
    }
    assert.ok(!/<image\b[^>]*api\.mockupPhotoImageUrl/.test(src), `${file}: an SVG <image> cannot load lazily`);
  }
  assert.ok(checked >= 20, `only ${checked} image calls found`);
});
