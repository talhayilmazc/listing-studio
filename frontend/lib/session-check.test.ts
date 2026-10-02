// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkSession } from "./session-check.ts";

const answering = (status: number) => async () => new Response(status === 204 ? null : "{}", { status });

test("a live session is valid, a dead one invalid", async () => {
  assert.equal(await checkSession("http://api:8000", "tok", {}, answering(204)), "valid");
  assert.equal(await checkSession("http://api:8000", "tok", {}, answering(401)), "invalid");
  assert.equal(await checkSession("http://api:8000", "", {}, answering(204)), "invalid");
});

test("an API that cannot say never signs anyone out", async () => {
  assert.equal(await checkSession("http://api:8000", "tok", {}, answering(429)), "unknown");
  assert.equal(await checkSession("http://api:8000", "tok", {}, answering(502)), "unknown");
  const down = async () => {
    throw new TypeError("fetch failed");
  };
  assert.equal(await checkSession("http://api:8000", "tok", {}, down), "unknown");
  const slow = (_: string, init: RequestInit) =>
    new Promise<Response>((_, reject) => init.signal?.addEventListener("abort", () => reject(new Error("aborted"))));
  assert.equal(await checkSession("http://api:8000", "tok", {}, slow, 50), "unknown");
});

test("it sends only the session cookie and the visitor's own headers, to the internal API", async () => {
  let seen: { url: string; init: RequestInit } | null = null;
  const spy = async (url: string, init: RequestInit) => {
    seen = { url, init };
    return new Response(null, { status: 204 });
  };
  await checkSession("http://api:8000/", "abc", { "cf-connecting-ip": "203.0.113.9" }, spy);
  assert.equal(seen!.url, "http://api:8000/api/account/session");
  assert.deepEqual(seen!.init.headers, { cookie: "session=abc", "cf-connecting-ip": "203.0.113.9" });
});
