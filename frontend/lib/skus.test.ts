// The same rules as backend/tests/test_skus.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { skuProblem, withAffixes } from "./skus.ts";

test("Etsy's SKU rules", () => {
  assert.equal(skuProblem("  BR5229-S "), null);
  assert.equal(skuProblem("x".repeat(32)), null);
  assert.match(skuProblem("x".repeat(33)) ?? "", /at most 32/);
  for (const bad of ["BR$5229", "BR^1", "BR`1", "BR\t1"]) assert.match(skuProblem(bad) ?? "", /does not accept/);
  assert.match(skuProblem("   ") ?? "", /Enter a SKU/);
});

test("prefix and suffix for the selected", () => {
  assert.equal(withAffixes("BR5229", "", "", "-CC"), "BR5229-CC");
  assert.equal(withAffixes("BR5229", "CC7001", "X-", ""), "X-CC7001");
});
