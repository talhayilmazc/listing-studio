// What a ticked shop still needs, and the one thing the seller can do about it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { anyChecking, needAction, needsHeading } from "./shopNeeds.ts";
import type { ShopNeed } from "./types.ts";

const need = (over: Partial<ShopNeed>): ShopNeed => ({
  kind: "profile", message: "", listings: 1, blocking: true, profile_id: "p1", profile_name: "Hoodie", setup: false, checking: false, ...over,
});

test("each need has one action", () => {
  assert.equal(needAction(need({ kind: "profile", setup: true })), "setup"); // not set up there: one click
  assert.equal(needAction(need({ kind: "checking", checking: true })), "wait"); // being set up now
  assert.equal(needAction(need({ kind: "shipping_profile", setup: true })), "profiles"); // set up, a shipping profile missing
  assert.equal(needAction(need({ kind: "return_policy" })), "profiles");
  assert.equal(needAction(need({ kind: "section", blocking: false, profile_id: null })), "card");
  assert.equal(needAction(need({ kind: "other" })), null); // the message says what to do
  assert.equal(needAction(need({ kind: "profile", setup: true, profile_id: null })), null);
});

test("the heading says whether drafts are held back", () => {
  assert.equal(
    needsHeading([need({ kind: "shipping_profile", listings: 3 }), need({ kind: "return_policy", listings: 3 })]),
    "No draft is created here for 3 listings until this is settled:",
  );
  assert.equal(needsHeading([need({ listings: 1 })]), "No draft is created here for 1 listing until this is settled:");
  assert.equal(needsHeading([need({ kind: "section", blocking: false })]), "Drafts are created here, without this:");
});

test("the page looks again only while something is being set up", () => {
  assert.equal(anyChecking([{ needs: [need({ kind: "checking", checking: true })] }]), true);
  assert.equal(anyChecking([{ needs: [need({ kind: "shipping_profile" })] }, { needs: [] }]), false);
});
