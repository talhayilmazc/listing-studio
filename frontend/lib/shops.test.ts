// What the seller is told after disconnecting a shop.
import assert from "node:assert/strict";
import { test } from "node:test";
import { disconnectedNotice } from "./shops.ts";
import { CONSENT_STATEMENT, PROVIDER_DISCLAIMER, RETENTION, SCOPE_REASONS, TERMS_VERSION, TRADEMARK_NOTICE } from "./legal.ts";

test("disconnecting says the tokens and data are gone, and exactly what was cancelled", () => {
  const said = disconnectedNotice({ shop_name: "Frost Tees", queued_jobs: 2, scheduled_publishes: 1, planned_drafts: 3 });
  assert.match(said, /^Frost Tees is disconnected\. Its tokens and the Etsy data we held for it are deleted/);
  assert.match(said, /Cancelled with it: 2 queued jobs, 1 scheduled go-live, 3 planned drafts\./);
  assert.match(said, /Listings already on Etsy are not touched\.$/);
});

test("nothing waiting is said as nothing, not left out", () => {
  const said = disconnectedNotice({ shop_name: null, queued_jobs: 0, scheduled_publishes: 0, planned_drafts: 0 });
  assert.match(said, /^The shop is disconnected\./);
  assert.match(said, /Nothing was queued or scheduled for it\.$/);
});

test("the legal constants are the exact texts", () => {
  assert.equal(
    TRADEMARK_NOTICE,
    "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.",
  );
  assert.equal(PROVIDER_DISCLAIMER, "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO.");
  assert.equal(CONSENT_STATEMENT, "I agree to the Terms of Service and Privacy Policy");
  assert.match(TERMS_VERSION, /^\d{4}-\d{2}-\d{2}$/);
  // The figures the Privacy Policy states (the backend test compares each with its setting).
  assert.deepEqual(
    [RETENTION.listingCacheHours, RETENTION.otherEtsyContentHours, RETENTION.uploadsAfterDraftsDays, RETENTION.uploadsOtherwiseDays, RETENTION.analyticsMonths],
    [6, 24, 3, 30, 24],
  );
  assert.deepEqual(SCOPE_REASONS.map((s) => s.scope), ["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"]);
  for (const s of SCOPE_REASONS) assert.ok(s.reason.length > 40, s.scope);
});
