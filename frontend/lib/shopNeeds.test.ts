// What a ticked shop still needs, and the one thing the seller can do about it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { anyChecking, needAction, needsHeading, setupItems } from "./shopNeeds.ts";
import type { ShopNeed } from "./types.ts";

const need = (over: Partial<ShopNeed>): ShopNeed => ({
  kind: "profile", message: "", listings: 1, blocking: true, profile_id: "p1", profile_name: "Hoodie", setup: false, checking: false, ...over,
});

test("each need has one action", () => {
  assert.equal(needAction(need({ kind: "profile", setup: true })), "setup"); // not set up there: one click
  assert.equal(needAction(need({ kind: "checking", checking: true })), "wait"); // being set up now
  // Set up there but one of the shop's own settings missing: one click sets it up in that shop.
  assert.equal(needAction(need({ kind: "readiness_state", setup: true })), "setup");
  assert.equal(needAction(need({ kind: "shipping_profile", setup: true })), "setup");
  assert.equal(needAction(need({ kind: "return_policy" })), "setup"); // chosen in the setup dialog, right here
  assert.equal(needAction(need({ kind: "section", blocking: false, profile_id: null })), "card");
  assert.equal(needAction(need({ kind: "other" })), null); // the message says what to do
  assert.equal(needAction(need({ kind: "profile", setup: true, profile_id: null })), null);
});

test("bug 3: every shop that needs the profile set up goes into one dialog, per profile", () => {
  const cols = [
    { connection_id: "harbor", needs: [need({ kind: "shipping_profile", profile_id: "tee", profile_name: "Standard Tee" }),
                                       need({ kind: "profile", profile_id: "crew", profile_name: "Crewneck" })] },
    { connection_id: "maple", needs: [need({ kind: "readiness_state", profile_id: "tee", profile_name: "Standard Tee" })] },
    { connection_id: "copper", needs: [need({ kind: "checking", checking: true, profile_id: "tee" })] },
    { connection_id: "pine", needs: [need({ kind: "section", blocking: false, profile_id: null })] },
  ];
  assert.deepEqual(setupItems(cols), [
    { profileId: "tee", profileName: "Standard Tee", shopIds: ["harbor", "maple"] },
    { profileId: "crew", profileName: "Crewneck", shopIds: ["harbor"] },
  ]);
  assert.deepEqual(setupItems(cols, ["maple"]), [{ profileId: "tee", profileName: "Standard Tee", shopIds: ["maple"] }]);
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
