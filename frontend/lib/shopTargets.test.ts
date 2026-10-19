// Picking a group or single shops: groups are shortcuts for their shops; a shop in
// two groups is chosen once; nothing is chosen by default.
import assert from "node:assert/strict";
import { test } from "node:test";
import { groupState, inOrder, toggleGroup, toggleShop } from "./shopTargets.ts";

const order = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9"];
const groupA = ["s1", "s2", "s3", "s4"];
const groupB = ["s4", "s5", "s6", "s7", "s8"]; // s4 is in both

test("choosing a group chooses each of its shops once, in the account's order", () => {
  let chosen: string[] = [];
  chosen = inOrder(toggleGroup(chosen, groupB, order, true), order);
  chosen = inOrder(toggleGroup(chosen, groupA, order, true), order);
  assert.deepEqual(chosen, ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]);
  assert.equal(groupState(chosen, groupA, order), "all");
  assert.equal(groupState([], groupA, order), "none"); // nothing by default
});

test("single shops and groups mix; a partly chosen group says so", () => {
  const chosen = toggleShop(toggleShop([], "s9", true), "s2", true);
  assert.equal(groupState(chosen, groupA, order), "some");
  assert.deepEqual(inOrder(toggleGroup(chosen, groupA, order, false), order), ["s9"]);
});

test("a shop that cannot be chosen (the profile's main shop) is left out of its group", () => {
  const available = order.filter((id) => id !== "s1");
  const chosen = toggleGroup([], groupA, available, true);
  assert.deepEqual(chosen, ["s2", "s3", "s4"]);
  assert.equal(groupState(chosen, groupA, available), "all");
  assert.equal(groupState([], ["s1"], available), "none");
});
