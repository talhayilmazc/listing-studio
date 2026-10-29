// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { throttle, worthReporting } from "./sellerEvents.ts";

test("every failed change is reported; loads only when the server failed or could not be reached", () => {
  assert.equal(worthReporting("POST", "/api/batches/b/another-shop", 409), true);
  assert.equal(worthReporting("PATCH", "/api/content/c", 422), true);
  assert.equal(worthReporting("GET", "/api/batches", 0), true);
  assert.equal(worthReporting("GET", "/api/batches", 503), true);
  assert.equal(worthReporting("GET", "/api/batches/x", 404), false);
  assert.equal(worthReporting("POST", "/api/account/login", 401), false); // signed out is not an error
  assert.equal(worthReporting("POST", "/api/events", 500), false); // never reports its own report
});

test("at most twenty a minute from one tab", () => {
  let clock = 0;
  const allowed = throttle(20, () => clock);
  assert.equal(Array.from({ length: 25 }, allowed).filter(Boolean).length, 20);
  clock = 60_001;
  assert.equal(allowed(), true);
});

test("the request path reports failures and signed-in pages report errors; no request body is sent", () => {
  const api = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
  assert.ok(api.includes('report({ kind: "action_failed", page: window.location.pathname, action: `${method} ${url.split("?")[0]}`'));
  const events = readFileSync(new URL("./sellerEvents.ts", import.meta.url), "utf8");
  assert.ok(!events.includes("init.body") && !api.includes("message: init.body"));
  const session = readFileSync(new URL("../components/SessionProvider.tsx", import.meta.url), "utf8");
  assert.ok(session.includes("installErrorReporting()"));
});
