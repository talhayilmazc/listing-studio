// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { TOO_LONG, connectBanner } from "./connectStatus.ts";

test("every refused connection says why in its own words and offers Try again", () => {
  for (const status of ["no_binding", "expired", "state_mismatch", "reused", "unavailable", "denied", "etsy_error", "taken"]) {
    const b = connectBanner(status);
    assert.ok(b && b.retry && b.text, status);
  }
  assert.equal(connectBanner("no_binding")?.text, TOO_LONG);
  assert.equal(TOO_LONG, "The connection took too long or started in another browser. Press Connect a shop again.");
  assert.match(connectBanner("other_account", "t***@example.com")!.text, /started by t\*\*\*@example\.com/);
  assert.equal(connectBanner("connected")?.retry, false);
});

test("a signed-in seller is never told they are not signed in", () => {
  for (const status of ["no_binding", "expired", "wrong_account", "state_mismatch", "reused", "other_account"]) {
    assert.doesNotMatch(connectBanner(status)!.text.toLowerCase(), /not signed in/, status);
  }
  const page = readFileSync(new URL("../app/(app)/connect/page.tsx", import.meta.url), "utf8");
  assert.ok(!page.toLowerCase().includes("not signed in"));
  assert.ok(page.includes("connectBanner(banner, who)") && page.includes('data-testid="connect-retry"'));
});
