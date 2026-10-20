// The setup dialog: matched settings preselected, a copy preselected where the shop
// has no match, every choice changeable, and only changes sent on confirm.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  COPY, anyFailure, choiceKey, choiceOptions, confirmLabel, defaultChoice, failureActions, pickValue, shopChoices, toStart,
  totals, useInShops, willUse,
} from "./shopSetup.ts";
import type { SettingUse, ShopSetup } from "./types.ts";

const setting = (over: Partial<SettingUse>): SettingUse => ({
  resource: "shipping_profile", label: "Shipping profile", source_label: "Standard US shipping", linked_ids: [],
  linked_label: null, how: null, default: "choose", reason: null, creatable: false, create_summary: null, requests: 0,
  options: [], ...over,
});

const matched = setting({
  linked_ids: [11], linked_label: "Standard US shipping", how: "same name", default: "keep",
  options: [{ id: 11, label: "Standard US shipping" }, { id: 12, label: "Free shipping over $35" }],
});
const noMatch = setting({
  resource: "return_policy", label: "Return policy", source_label: "returns, exchanges, 30 days", default: "copy",
  creatable: true, requests: 1, reason: "no match in this shop", options: [{ id: 21, label: "no returns, no exchanges" }],
});
const ambiguous = setting({
  resource: "readiness_state", label: "Processing profile", source_label: "Made to order, 2-4 days", default: "choose",
  reason: "several in this shop match; choose one",
  options: [{ id: 31, label: "Made to order, 2-4 days (tees)" }, { id: 32, label: "Made to order, 2-4 days (hoodies)" }],
});

const shop = (id: string, settings: SettingUse[], status = "incomplete"): ShopSetup => ({
  connection_id: id, shop_name: id, settings, open: [], choices_expired: false,
  link: { connection_id: id, shop_name: id, main: false, ready: status === "ready", status, reason: null, missing: [], checked_at: null },
});

test("the shop's matching setting is used by default, a copy where there is no match", () => {
  assert.equal(defaultChoice(matched), pickValue([11]));
  assert.equal(defaultChoice(noMatch), COPY);
  assert.equal(defaultChoice(ambiguous), ""); // two match equally: never guessed
  assert.equal(willUse(matched, defaultChoice(matched), "Harbor"), "Uses Standard US shipping (same name).");
  assert.match(willUse(noMatch, COPY, "Harbor"), /copy of returns, exchanges, 30 days is created in Harbor when you confirm \(1 Etsy request\)/);
});

test("every choice can be changed: the shop's own settings and the copy are offered", () => {
  const labels = choiceOptions(noMatch).map((o) => o.label);
  assert.deepEqual(labels, ["no returns, no exchanges", "Create a copy of returns, exchanges, 30 days (1 request)"]);
  const matchedLabels = choiceOptions(matched).map((o) => o.label);
  assert.deepEqual(matchedLabels, ["Standard US shipping (same name)", "Free shipping over $35"]);
  assert.equal(willUse(matched, pickValue([12]), "Harbor"), "Uses Free shipping over $35 (your choice).");
});

test("confirm sends only what changes, and nothing is created unless a copy is chosen", () => {
  const s = shop("h", [matched, noMatch, ambiguous]);
  // Defaults: the match is kept (not sent), the copy is sent, the ambiguous one waits.
  assert.deepEqual(shopChoices(s, {}), [{ connection_id: "h", resource: "return_policy", use: "copy", ids: [] }]);
  const changed = {
    [choiceKey("h", "shipping_profile")]: pickValue([12]),
    [choiceKey("h", "return_policy")]: pickValue([21]),
    [choiceKey("h", "readiness_state")]: pickValue([32]),
  };
  assert.deepEqual(shopChoices(s, changed), [
    { connection_id: "h", resource: "shipping_profile", use: "pick", ids: [12] },
    { connection_id: "h", resource: "return_policy", use: "pick", ids: [21] },
    { connection_id: "h", resource: "readiness_state", use: "pick", ids: [32] },
  ]);
});

test("totals across several shops: copies, their requests, and what is still to choose", () => {
  const shops = [shop("a", [matched, noMatch, ambiguous]), shop("b", [noMatch]), shop("c", [matched], "ready")];
  const t = totals(shops, {});
  assert.deepEqual(t, { copies: 2, requests: 2, unresolved: 1, ready: 2, shops: 3 });
  assert.equal(confirmLabel(t), "Confirm: create 2 copies and set up 3 shops");
  assert.equal(confirmLabel(totals([shop("c", [matched], "ready")], {})), "Confirm and set up 1 shop");
});

test("shops with no link or a failed one are started; expired choices are read again", () => {
  const failed = shop("f", []);
  failed.link.status = "error";
  const expired = shop("e", [noMatch]);
  expired.choices_expired = true;
  assert.deepEqual(toStart([failed, expired, shop("ok", [matched])], ["new", "f", "e", "ok"]), { start: ["new", "f"], again: ["e"] });
});

test("bug 1: Chosen shops offers every shop but the main one, whatever its setup state", () => {
  const shops = ["Northwind", "Juniper", "Harbor", "Maple", "Pinecrest"].map((n) => ({ id: n, name: n }));
  const links = [
    { connection_id: "Northwind", main: true, ready: true, status: "ready" },
    { connection_id: "Juniper", main: false, ready: true, status: "ready" },
    { connection_id: "Harbor", main: false, ready: false, status: "incomplete" },
    { connection_id: "Maple", main: false, ready: false, status: "error" },
  ];
  // Before the fix only Pinecrest (no setup yet) was offered: Harbor and Maple could
  // not be finished or retried from "Chosen shops", and with every shop tried once
  // the button was disabled.
  assert.deepEqual(useInShops(shops, links), [
    { id: "Juniper", name: "Juniper", note: "set up" },
    { id: "Harbor", name: "Harbor", note: "needs a choice" },
    { id: "Maple", name: "Maple", note: "failed: try again" },
    { id: "Pinecrest", name: "Pinecrest", note: null },
  ]);
});


// --- A copy Etsy refused ---------------------------------------------------------------------

test("a refused copy is not preselected again, and says why with the action that fixes it", () => {
  const failure = { reason: 'Etsy refused it: "Insufficient scope". Reconnect PCS on the Connect page…', action: "reconnect" as const };
  const s = setting({ default: "choose", creatable: true, requests: 3, failure });
  assert.equal(defaultChoice(s), ""); // the seller picks, or copies again on purpose
  assert.ok(choiceOptions(s).some((o) => o.value === COPY)); // still possible after a fix
  assert.deepEqual(failureActions(failure, "PCS"), [
    { kind: "reconnect", href: "/connect", label: "Reconnect PCS" },
    { kind: "refresh", label: "Refresh PCS's settings" },
  ]);
  for (const action of ["fix", "finish", "refresh", "manual"] as const) {
    assert.deepEqual(failureActions({ reason: "x", action }, "PCS"), [{ kind: "refresh", label: "Refresh PCS's settings" }]);
  }
});

test("a refused copy keeps the dialog on the choices", () => {
  const ok = { connection_id: "s1", settings: [setting({ default: "copy", creatable: true })] } as unknown as ShopSetup;
  const refused = { connection_id: "s2", settings: [setting({ failure: { reason: "r", action: "manual" } })] } as unknown as ShopSetup;
  assert.equal(anyFailure([ok]), false);
  assert.equal(anyFailure([ok, refused]), true);
});

test("the dialog has a Refresh for each shop and shows the refusal on its setting", async () => {
  const { readFileSync } = await import("node:fs");
  const dialog = readFileSync(new URL("../components/ShopSetupDialog.tsx", import.meta.url), "utf8");
  assert.ok(dialog.includes('data-testid="setup-refresh"') && dialog.includes("api.checkShopAgain("));
  assert.ok(dialog.includes('data-testid="setup-failure"') && dialog.includes("failureActions("));
  assert.ok(!dialog.includes("failed; try again"), "the refusal is Etsy's reason, not a fixed sentence");
});
