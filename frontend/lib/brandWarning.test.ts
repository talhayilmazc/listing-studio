// A brand the filter did not stop is said on the card, the workspace row, the send summary and the
// Where and when panel, in the server's own words, and it never stops the button.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { sendSummary } from "./anotherShop.ts";
import { brandWarning } from "./brandSpot.ts";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const WARNING = brandWarning("Disney");

function row(over: object) {
  return { content_id: "a", state: "new", errors: [], warnings: [], approved: true, sku: "A1", sku_own: false, text_own: [], title: "T", group_key: "A1", profile_name: "P", ...over };
}

test("the send summary says each brand once and still counts the listing as going", () => {
  const rows = [row({ content_id: "a", brand_warnings: [WARNING] }), row({ content_id: "b", brand_warnings: [WARNING] }), row({ content_id: "c" })];
  const s = sendSummary({ rows, refused: [] } as never, ["a", "b", "c"]);
  assert.deepEqual(s.brands, [WARNING]);
  assert.equal(s.going, 3);
  assert.deepEqual(s.notGoing, []);
});

test("a listing not chosen says nothing in the summary", () => {
  const s = sendSummary({ rows: [row({ content_id: "a", brand_warnings: [WARNING] })], refused: [] } as never, []);
  assert.deepEqual(s.brands, []);
});

test("each screen shows the server's words and writes no sentence of its own", () => {
  for (const [path, testid] of [["components/ReviewCard.tsx", "brand-warnings"], ["components/ShopWorkspace.tsx", "workspace-row-brands"],
    ["components/ShopWorkspace.tsx", "workspace-summary-brands"], ["components/WhereAndWhen.tsx", "send-brands"]]) {
    const source = read(path);
    assert.ok(source.includes(`data-testid="${testid}"`), `${path}: ${testid}`);
    assert.ok(!source.includes("is a trademark"), `${path} writes the sentence itself`);
  }
});

test("the warning does not stop the send button", () => {
  const workspace = read("components/ShopWorkspace.tsx");
  const blocked = workspace.slice(workspace.indexOf("const blocked ="), workspace.indexOf("return (", workspace.indexOf("const blocked =")));
  assert.ok(!blocked.includes("brands"));
});
