// Profiles made and edited by hand (feature/manual-profiles): the form's helpers, the sidebar entry, and what
// the page and the card show for a profile that has no reference listing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ATTRIBUTES_FAILED, ATTRIBUTES_WAIT_MS, FIELD_GROUP, GROUPS, buildVariations, emptyValues, isOwn, list, missing, missingLine,
  money, optionsOf, refusal, rounded, sentence, shown, toNumber, withDefaults,
  type FormOptions, type FormValues,
} from "./profileForm.ts";
import { tabsFor } from "./sectionTabs.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const options = (over: Partial<FormOptions> = {}): FormOptions => ({
  shop_id: "s", shop_name: "GLHCUSTOMWEAR", reading: false, currency: "USD",
  shipping: [{ id: 1, label: "Standard", type: "manual" }], returns: [{ id: 2, label: "No returns" }, { id: 3, label: "Returns within 30 days" }],
  readiness: [{ id: 4, label: "Made to order, 1-3 days" }], partners: [], partner_note: null, who_made: [], when_made: [], ...over,
});

test("Profiles is in the sidebar directly under Shops; Settings keeps its Profiles tab (one page)", () => {
  const rail = read("components/Rail.tsx");
  const labels = [...rail.matchAll(/label: "([^"]+)"/g)].map((m) => m[1]);
  assert.equal(labels[labels.indexOf("Shops") + 1], "Profiles");
  assert.ok(rail.includes('{ href: "/profiles", label: "Profiles", icon: IconTag }'));
  assert.deepEqual(tabsFor("/settings")?.tabs.map((t) => t.href), ["/settings", "/profiles", "/mockups/templates"]);
  assert.equal(tabsFor("/profiles")?.current, "/profiles");
});

test("a new profile starts with sensible values, and a setting the shop has one of is chosen", () => {
  const start = emptyValues();
  assert.deepEqual([start.who_made, start.when_made, start.is_supply, start.quantity], ["i_did", "made_to_order", false, 999]);
  const filled = withDefaults(start, options());
  assert.deepEqual([filled.shipping_profile_id, filled.return_policy_id, filled.readiness_state_id], [1, null, 4]);  // two return policies: asked
  assert.equal(withDefaults({ ...start, shipping_profile_id: 9 }, options()).shipping_profile_id, 9);  // a choice is never replaced
});

test("variations: every combination once, in the order typed; rows that were there keep their price", () => {
  const table = buildVariations(["Size", "Colour"], [["S", "M"], ["Black", "White"]], null, 20, 999)!;
  assert.deepEqual(table.properties, ["Size", "Colour"]);
  assert.deepEqual(table.rows.map((r) => r.values.join("/")), ["S/Black", "S/White", "M/Black", "M/White"]);
  assert.ok(table.rows.every((r) => r.price === 20 && r.quantity === 999 && r.enabled));
  table.rows[2].price = 23;
  const more = buildVariations(["Size", "Colour"], [["S", "M", "L"], ["Black", "White"]], table, 20, 999)!;
  assert.equal(more.rows.length, 6);
  assert.equal(more.rows.find((r) => r.values.join("/") === "M/Black")!.price, 23);  // kept
  assert.equal(more.rows.find((r) => r.values.join("/") === "L/Black")!.price, 20);  // new: the profile's own price
  assert.deepEqual(optionsOf(more), [["S", "M", "L"], ["Black", "White"]]);
  assert.equal(buildVariations(["", ""], [[], []], table, 20, 1), null);  // none: a listing without variations
  assert.deepEqual(buildVariations(["Size", ""], [["One size"], []], null, 5, 1)!.properties, ["Size"]);
  assert.deepEqual(list(" Cotton , Polyester ,, "), ["Cotton", "Polyester"]);
  assert.deepEqual([toNumber("24,5"), toNumber(" 7 "), toNumber(""), toNumber("abc")], [24.5, 7, null, null]);
});

test("a refused save is the server's own sentences, each under its field's group", () => {
  const error = new Error(JSON.stringify({ message: "Not saved: Give a price: Etsy needs one on every listing. (1 more)",
    fields: { price: "Give a price: Etsy needs one on every listing.", description: "Write a description: Etsy needs one on every listing." } }));
  const r = refusal(error);
  assert.equal(r.fields.price, "Give a price: Etsy needs one on every listing.");
  assert.deepEqual([FIELD_GROUP.price, FIELD_GROUP.description, FIELD_GROUP.production_partner_ids], ["Price & variations", "Text", "Shipping & policies"]);
  assert.deepEqual(refusal(new Error("Could not reach Listyro.")), { message: "Could not reach Listyro.", fields: {} });
  assert.deepEqual([...GROUPS], ["Basics", "Price & variations", "Shipping & policies", "Details", "Text"]);
  for (const field of Object.keys(FIELD_GROUP)) assert.ok((GROUPS as readonly string[]).includes(FIELD_GROUP[field]), field);
});

test("your value: only on a profile that follows a listing, with the listing's own value said beside it", () => {
  assert.equal(isOwn("price", ["price"], false), true);
  assert.equal(isOwn("price", ["price"], true), false);  // a profile made by hand: every value is its own, none is marked
  assert.equal(isOwn("materials", ["price"], false), false);
  assert.equal(shown("return_policy_id", 3, options()), "Returns within 30 days");
  assert.equal(shown("materials", ["Cotton", "Linen"]), "Cotton, Linen");
  assert.equal(shown("variations", { properties: ["Size"], rows: [{ values: ["S"] }, { values: ["M"] }] }), "Size: 2 options");
  assert.equal(shown("price", null), "none");
  const form = read("components/ProfileForm.tsx");
  for (const text of ["your value", "Use the listing&apos;s value again", "follows the listing", "Stop following the listing", "This cannot be undone."]) {
    assert.ok(form.includes(text), text);
  }
  for (const id of ["profile-form", "profile-form-save", "form-category-search", "variation-price", "form-partner-note", "stop-following-yes"]) {
    assert.ok(form.includes(`"${id}"`) || form.includes(`\`${id}`), id);
  }
});

test("the Profiles page offers three ways to make a profile; a profile made by hand never shows Refresh", () => {
  const entry = read("components/NewProfile.tsx");
  for (const text of ["From one of my listings", "Start from scratch", "Copy from another profile"]) assert.ok(entry.includes(text), text);
  assert.ok(read("app/(app)/profiles/page.tsx").includes("<NewProfile "));
  const card = read("components/ProfileCard.tsx");
  assert.ok(card.includes("{!manual && (") && card.includes('data-testid="profile-edit"'));
  assert.ok(card.includes("your own values · never needs a refresh"));
  // The empty-shop note is the server's sentence (pipeline/links.py), never retyped here.
  assert.ok(!read("components/ProfileForm.tsx").includes("Shop Manager → Settings → Production partners"));
});

test("a price is shown with the currency's decimals and kept rounded to them", () => {
  assert.equal(money(24.5, "USD"), "24.50");
  assert.equal(money(24, "EUR"), "24.00");
  assert.equal(money(24.5, null), "24.50");
  assert.equal(money(2400, "JPY"), "2400");
  assert.equal(money(null, "USD"), "");
  assert.equal(rounded(24.999, "USD"), 25);
  assert.equal(rounded(24.505, "USD"), 24.51);
  assert.equal(rounded(null, "USD"), null);
  assert.equal(shown("price", 24.5, { currency: "USD" } as FormOptions), "24.50 USD");
});

test("a shop name that ends with a full stop is not given a second one", () => {
  assert.equal(sentence("Juniper Print Co."), "Juniper Print Co.");
  assert.equal(sentence("Northwind Tees"), "Northwind Tees.");
});

test("what Etsy needs on a draft is named until it is filled", () => {
  const blank = emptyValues();
  assert.deepEqual(missing("", blank, { manual: true }), [
    "Profile name", "Category", "Price", "Shipping profile", "Return policy", "Processing profile", "Description",
  ]);
  assert.equal(missingLine(["Category", "Price"]), "Still needed: Category, Price.");
  const full: FormValues = {
    ...blank, taxonomy_id: 9001, price: 24.5, shipping_profile_id: 1, return_policy_id: 2, readiness_state_id: 3, description: "A soft tee.",
  };
  assert.deepEqual(missing("Tees", full, { manual: true }), []);
  assert.deepEqual(missing("Tees", { ...full, quantity: null }, { manual: true }), ["Quantity"]);
  // Every option priced: the profile needs no price of its own.
  const table = { properties: ["Size"], rows: [{ values: ["S"], price: 20, quantity: 5, enabled: true }] };
  assert.deepEqual(missing("Tees", { ...full, price: null, variations: table }, { manual: true }), []);
  assert.deepEqual(
    missing("Tees", { ...full, price: null, variations: { ...table, rows: [{ ...table.rows[0], price: null }] } }, { manual: true }),
    ["Price"],
  );
  // A profile that follows a listing: only what the seller emptied (the listing has one) is missing.
  const listing = { ...full, readiness_state_id: null, quantity: null };
  assert.deepEqual(missing("Tees", { ...listing, return_policy_id: null }, { manual: false, listing }), ["Return policy"]);
  assert.deepEqual(missing("Tees", listing, { manual: false, listing }), []);
});

test("the form says it could not read a category's attributes after 15 seconds", () => {
  assert.equal(ATTRIBUTES_WAIT_MS, 15_000);
  assert.equal(ATTRIBUTES_FAILED, "Couldn't read this category's attributes.");
});
