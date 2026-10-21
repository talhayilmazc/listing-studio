// "Delete my sales history" and "Updated <time>" on the profit report (Part B3; backend
// pipeline/sales_history.py, tests/test_sales_history.py), and the Privacy Policy's own date.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { AFTER_DELETE, CONFIRM_QUESTION, DELETE_LABEL, WHAT_IS_DELETED, deletedLine, updatedLine } from "./salesHistory.ts";
import { LAST_UPDATED, PRIVACY_UPDATED, RETENTION, TERMS_VERSION } from "./legal.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (f: string) => fs.readFileSync(path.join(FRONTEND, f), "utf8");

test("the confirmation says what is deleted, in the server's words, and what happens after", () => {
  const backend = fs.readFileSync(path.join(FRONTEND, "..", "backend", "app", "pipeline", "sales_history.py"), "utf8");
  const said = /WHAT_IS_DELETED = \(\s*([\s\S]*?)\n\)/.exec(backend)![1].split("\n").map((l) => l.trim().replace(/^"|"$/g, "")).join("");
  assert.equal(WHAT_IS_DELETED, said);
  assert.equal(DELETE_LABEL, "Delete my sales history");
  assert.match(CONFIRM_QUESTION, /\?$/);
  assert.match(AFTER_DELETE, /store nothing from before today/);
});

test("the panel asks first, then deletes, and says when the figures were updated", () => {
  const panel = read("components/analytics/SalesPanel.tsx");
  const api = read("lib/api.ts");
  // A first press only opens the confirmation; the delete is the confirmation's own button.
  assert.match(panel, /onClick=\{\(\) => setConfirming\(true\)\}[\s\S]*data-testid="sales-history-delete"/);
  assert.match(panel, /onClick=\{deleteHistory\} disabled=\{busy\} data-testid="sales-history-confirm-delete"/);
  assert.match(panel, /role="alertdialog"/);
  assert.match(api, /\/analytics\/sales\/history\/delete\$\{shopQuery\(shop\)\}`, \{ method: "POST", body: JSON\.stringify\(\{ confirm: true \}\) \}/);
  assert.match(panel, /data-testid="sales-updated"/);
  assert.match(panel, /updatedLine\(sync\.updated_at \?\? sync\.synced_at, .*RETENTION\.salesRefreshDays\)/);
  const fmt = (iso: string) => `at ${iso}`;
  assert.equal(updatedLine("2026-10-19T05:10:00Z", fmt, RETENTION.salesRefreshDays),
    "Updated at 2026-10-19T05:10:00Z · the last 30 days are read again every night");
  assert.match(updatedLine(null, fmt, 30), /^Not updated yet/);
  assert.equal(deletedLine("2026-10-19"), "You deleted this shop's earlier sales history; the report starts on Oct 19, 2026.");
  assert.equal(deletedLine(null), "");
});

test("the Privacy Policy has its own date; the terms version stays", () => {
  assert.equal(PRIVACY_UPDATED, "21 October 2026");
  assert.equal(TERMS_VERSION, "2026-10-14");
  assert.equal(LAST_UPDATED, "14 October 2026");
  const privacy = read("app/(marketing)/privacy/page.tsx");
  assert.match(privacy, /updated=\{PRIVACY_UPDATED\}/);
  assert.match(read("app/(marketing)/terms/page.tsx"), /updated=\{LAST_UPDATED\}/);
});
