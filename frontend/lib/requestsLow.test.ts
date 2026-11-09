// Home has no "Etsy requests today" card (Part F): only a warning under 20% left; the card is on Shops.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { requestsLow } from "./requestsLow.ts";

const q = (remaining: number, limit: number) => ({ ceiling: { remaining, limit } }) as any;

test("the warning shows under a fifth left, never at or above it", () => {
  assert.equal(requestsLow(q(20000, 20000)), false);
  assert.equal(requestsLow(q(4000, 20000)), false); // exactly 20%
  assert.equal(requestsLow(q(3999, 20000)), true);
  assert.equal(requestsLow(q(0, 20000)), true);
  assert.equal(requestsLow(q(0, 0)), false); // no limit known: nothing said
  assert.equal(requestsLow(null), false);
});

test("Home shows only the warning; the Shops page has the card", () => {
  const home = readFileSync(new URL("../app/(app)/dashboard/page.tsx", import.meta.url), "utf8");
  assert.ok(!home.includes("QuotaCard") && !home.includes("<EtsyRequestsCard"));
  assert.ok(home.includes("<RequestsLow quota={quota} />"));
  const shops = readFileSync(new URL("../app/(app)/connect/page.tsx", import.meta.url), "utf8");
  assert.ok(shops.includes("<EtsyRequestsCard />"));
  const card = readFileSync(new URL("../components/EtsyRequestsCard.tsx", import.meta.url), "utf8");
  assert.ok(card.includes("Etsy requests today"));
});
