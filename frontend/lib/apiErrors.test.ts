// Quality pass 2026-10: every failed request says what happened in plain words, with what to do.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { UNREACHABLE, messageFor } from "./apiErrors.ts";

test("the server's own sentence is kept", () => {
  assert.equal(messageFor(409, { detail: "The mockups are still being made." }), "The mockups are still being made.");
});

test("a validation list becomes one sentence, never raw JSON", () => {
  const msg = messageFor(422, { detail: [{ loc: ["body", "email"], msg: "value is not a valid email address", type: "x" },
    { loc: ["body", "tools", 0], msg: "Value error, unknown tool", type: "y" }] });
  assert.equal(msg, "Check what was sent: email: value is not a valid email address; tools.0: unknown tool.");
  assert.ok(!msg.includes("{") && !msg.includes("["));
});

test("a server error gives an action and the reference, not 'internal error'", () => {
  const msg = messageFor(500, { detail: "internal error", request_id: "abc123" });
  assert.match(msg, /Try again in a minute/);
  assert.match(msg, /abc123/);
  assert.ok(!msg.includes("internal error"));
});

test("an object detail stays JSON: the review card reads its fields", () => {
  assert.deepEqual(JSON.parse(messageFor(422, { detail: { errors: ["needs a title"] } })), { errors: ["needs a title"] });
});

test("statuses without a body still say something useful", () => {
  assert.match(messageFor(401, undefined), /Sign in again/);
  assert.match(messageFor(429, undefined), /Wait a minute/);
  assert.match(messageFor(413, undefined), /too large/);
  assert.match(UNREACHABLE, /Check your connection/);
});

test("every request path goes through it", () => {
  const api = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
  assert.ok(!api.includes("JSON.stringify(detail)"), "no raw JSON error messages");
  assert.ok(api.includes("messageFor(res.status") && api.includes("messageFor(xhr.status"));
});
