// A batch goes to the shop it was uploaded for, never to another shop silently (16 November 2026).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { profileNote } from "./profileShopNote.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const profile = (id: string, shop: string, shopName: string, template = "apparel") =>
  ({ id, name: `Profile ${id}`, connection_id: shop, shop_name: shopName, content_template: template });

test("the card says whose profile it is when the listing goes to another shop, and offers that shop's own", () => {
  const profiles = [profile("p1", "jph", "JPH"), profile("p2", "ilc", "ILC"), profile("p3", "opa", "OPA", "mug")];
  const toIlc = [{ shop_id: "ilc", shop_name: "ILC" }];
  assert.deepEqual(profileNote(profiles, "p1", toIlc),
    { text: "Profile is from JPH; this listing goes to ILC.", shop: "ILC", own: { id: "p2", name: "Profile p2" } });
  // The profile's own shop is among the destinations: nothing to say.
  assert.equal(profileNote(profiles, "p1", [{ shop_id: "jph", shop_name: "JPH" }, ...toIlc]), null);
  // No profile of that kind in the shop, or two of them: the note, and no guess.
  assert.equal(profileNote(profiles, "p1", [{ shop_id: "opa", shop_name: "OPA" }])?.own, null);
  assert.equal(profileNote([...profiles, profile("p4", "ilc", "ILC")], "p1", toIlc)?.own, null);
  // Several shops: named, no one-click profile.
  const two = profileNote(profiles, "p1", [...toIlc, { shop_id: "opa", shop_name: "OPA" }]);
  assert.equal(two?.text, "Profile is from JPH; this listing goes to ILC, OPA.");
  assert.equal(two?.own, null);
  assert.equal(profileNote(profiles, null, toIlc), null);
});

test("the upload is for the shop being worked on, and the panel says when listings go elsewhere", () => {
  assert.ok(read("../app/(app)/upload/page.tsx").includes("api.createBatch(workingOn?.id ?? null)"));
  const panel = read("../components/WhereAndWhen.tsx");
  assert.ok(panel.includes('data-testid="send-elsewhere"') && panel.includes("You are working on ${workingOn.name}; these go to ${elsewhere}."));
  assert.ok(panel.includes('data-testid="send-here"'), "one visible click sends them to the shop being worked on");
  assert.ok(read("../app/(app)/batches/[id]/review/page.tsx").includes('data-testid="card-profile-shop"'));
});
