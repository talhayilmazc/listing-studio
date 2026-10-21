// A reference listing in the seller's shops: its main shop's shipping, return and processing
// settings are used in every shop with no question (no dialog, no "Finish setup"); a shop is
// ready, preparing…, or one plain line; "change" picks another of a shop's own settings.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { CHANGEABLE, panelSettings, settingsLine, shopState, useInShops } from "./shopSetup.ts";
import type { SettingUse, ShopSetup } from "./types.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

const setting = (over: Partial<SettingUse>): SettingUse => ({
  resource: "shipping_profile", label: "Shipping profile", source_label: "MemoPaid", linked_ids: [],
  linked_label: null, how: null, default: "keep", reason: null, creatable: false, create_summary: null, requests: 0,
  options: [], ...over,
});

test("the card's one line names the main shop", () => {
  assert.equal(settingsLine("OPA"), "Shipping, returns and processing: same as OPA in every shop");
  assert.equal(settingsLine(null), "Shipping, returns and processing: same as the main shop in every shop");
});

test("a shop is ready, preparing…, or one plain line; never \"no match\" or \"Finish setup\"", () => {
  assert.deepEqual(shopState({ main: true, ready: true, status: "ready", reason: null }), { kind: "main", text: "main shop" });
  assert.deepEqual(shopState({ main: false, ready: true, status: "ready", reason: null }), { kind: "ready", text: "ready" });
  assert.deepEqual(shopState({ main: false, ready: false, status: "checking", reason: "x" }), { kind: "preparing", text: "preparing…" });
  const refused = 'Shipping profile: Etsy refused the copy of "MemoPaid": "Nope". Create it in PCS\'s Shop Manager, then press Refresh and pick it.';
  assert.deepEqual(shopState({ main: false, ready: false, status: "incomplete", reason: refused }), { kind: "problem", text: refused });
});

test("change offers each shop's own shipping, return and processing settings, with the one in use", () => {
  const shop = {
    connection_id: "pcs", shop_name: "PCS",
    settings: [
      setting({ linked_ids: [9001], linked_label: "MemoPaid", how: "created", options: [{ id: 9001, label: "MemoPaid" }, { id: 9002, label: "Express" }] }),
      setting({ resource: "return_policy", label: "Return policy", linked_ids: [7], options: [{ id: 8, label: "No returns" }] }),
      setting({ resource: "production_partners", label: "Production partners", linked_ids: [1, 2] }),
    ],
  } as unknown as ShopSetup;
  assert.deepEqual(panelSettings(shop), [
    { resource: "shipping_profile", label: "Shipping profile", value: 9001,
      options: [{ id: 9001, label: "MemoPaid" }, { id: 9002, label: "Express" }] },
    // The one in use is offered even when the shop's list (kept a while) does not have it yet.
    { resource: "return_policy", label: "Return policy", value: 7, options: [{ id: 7, label: "#7" }, { id: 8, label: "No returns" }] },
  ]);
  assert.deepEqual([...CHANGEABLE], ["shipping_profile", "return_policy", "readiness_state"]);
});

test("Use in offers every shop but the main one, whatever its state", () => {
  const shops = ["Northwind", "Juniper", "Harbor", "Maple", "Pinecrest"].map((n) => ({ id: n, name: n }));
  const links = [
    { connection_id: "Northwind", main: true, ready: true, status: "ready" },
    { connection_id: "Juniper", main: false, ready: true, status: "ready" },
    { connection_id: "Harbor", main: false, ready: false, status: "checking" },
    { connection_id: "Maple", main: false, ready: false, status: "incomplete" },
  ];
  assert.deepEqual(useInShops(shops, links), [
    { id: "Juniper", name: "Juniper", note: "ready" },
    { id: "Harbor", name: "Harbor", note: "preparing…" },
    { id: "Maple", name: "Maple", note: "needs a look" },
    { id: "Pinecrest", name: "Pinecrest", note: null },
  ]);
});

test("no setup dialog, no Finish setup, no Shops-page button; the card has the line and change", () => {
  const card = read("components/ProfileShops.tsx");
  assert.ok(card.includes("settingsLine(") && card.includes('data-testid="settings-change"') && card.includes("<ShopSettingsPanel"));
  assert.ok(card.includes("startSetup(") && card.includes("preparing…"));
  for (const gone of ["Finish setup", "ShopSetupDialog", "no match in this shop", "Use all reference listings here"]) {
    assert.ok(!card.includes(gone), gone);
  }
  assert.ok(!read("app/(app)/connect/page.tsx").includes("ShopProfilesSetup"));
  for (const page of ["app/(app)/batches/[id]/page.tsx", "app/(app)/batches/[id]/review/page.tsx", "components/Distribution.tsx",
                      "components/UnfinishedDrafts.tsx", "components/PublishMatrix.tsx"]) {
    assert.ok(!read(page).includes("ShopSetupDialog"), page);
  }
  const panel = read("components/ShopSettingsPanel.tsx");
  assert.ok(panel.includes("api.pickInShop(") && panel.includes("panelSettings("));
  assert.ok(read("app/(app)/profiles/page.tsx").includes("PROFILES_CHANGED"), "every card reloads when one changes");
});
